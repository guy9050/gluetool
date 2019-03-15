import os
import re
import tempfile
import json
from urllib2 import urlopen
from urlgrabber.grabber import urlgrab

import gluetool
from gluetool import GlueError, SoftGlueError
from gluetool.glue import DryRunLevels
from gluetool.log import log_blob, log_dict, format_dict
from gluetool.utils import cached_property, Command, check_for_commands, new_xml_element, GlueCommandError, \
    dict_update, Bunch, PatternMap
from libci.results import TestResult, publish_result

REQUIRED_CMDS = ['covscan']


def _unlink(filepath):
    try:
        os.unlink(filepath)

    # pylint: disable=broad-except
    except Exception as exc:
        raise GlueError('Unable to remove {}: {}'.format(filepath, exc))


class CovscanFailedError(SoftGlueError):
    def __init__(self, url):
        super(CovscanFailedError, self).__init__('Covscan testing failed, task did not pass')

        self.covscan_result_url = url

    # do not send this entry to Sentry
    @property
    def submit_to_sentry(self):

        return False


class NoCovscanBaselineFoundError(SoftGlueError):
    STATUS = 'SKIP'

    def __init__(self):
        super(NoCovscanBaselineFoundError, self).__init__('Could not find baseline for this build')


class CovscanTestResult(TestResult):
    # pylint: disable=too-few-public-methods

    def __init__(self, glue, overall_result, covscan_result, task, **kwargs):
        urls = kwargs.pop('urls', {})
        urls.update({
            'covscan_url': covscan_result.url,
            'brew_url': task.url
        })

        super(CovscanTestResult, self).__init__(glue, 'covscan', overall_result, urls=urls, **kwargs)

        self.fixed = len(covscan_result.fixed)
        self.added = len(covscan_result.added)
        self.baseline = task.latest

    @classmethod
    def _unserialize_from_json(cls, glue, input_data):
        covscan_result = Bunch(url=input_data['urls']['covscan_url'],
                               fixed=range(0, input_data['fixed']),
                               added=range(0, input_data['added']))

        task = Bunch(url=input_data['urls']['brew_url'], latest=input_data['baseline'])

        return CovscanTestResult(glue, input_data['overall_result'], covscan_result, task,
                                 ids=input_data['ids'], urls=input_data['urls'], payload=input_data['payload'])

    def _serialize_to_json(self):
        serialized = super(CovscanTestResult, self)._serialize_to_json()

        return dict_update(serialized, {
            'baseline': self.baseline,
            'fixed': self.fixed,
            'added': self.added
        })

    def _serialize_to_xunit_property_dict(self, parent, properties, names):
        if 'covscan_url' in properties:
            gluetool.utils.new_xml_element('property', parent, name='baseosci.url.covscan-run',
                                           value=properties.pop('covscan_url'))

        if 'brew_url' in properties:
            # just drop this one - it can be reconstructed from task ID anyway
            properties.pop('brew_url')

        super(CovscanTestResult, self)._serialize_to_xunit_property_dict(parent, properties, names)

    def _serialize_to_xunit(self):
        test_suite = super(CovscanTestResult, self)._serialize_to_xunit()

        self.glue.shared('covscan_xunit_serialize', test_suite, self)

        return test_suite


class CovscanResult(object):
    def __init__(self, module, task_id):
        self.module = module
        self.task_id = task_id
        self.url = 'http://cov01.lab.eng.brq.redhat.com/covscanhub/task/{}/'.format(task_id)

    def _fetch_diff(self, url):
        diff_json = urlopen(url).read()
        try:
            diff = json.loads(diff_json)
        except ValueError:
            raise CovscanFailedError(url)
        log_blob(self.module.debug, 'This is what we got from covscan', diff)
        defects = diff['defects']
        self.module.debug('Defects:\n{}\nfetched from {}'.format(format_dict(defects), url))
        return defects

    @cached_property
    def added(self):
        added_json_url = self.url + 'log/added.js?format=raw'
        added_defects = self._fetch_diff(added_json_url)
        return added_defects

    @cached_property
    def fixed(self):
        fixed_json_url = self.url + 'log/fixed.js?format=raw'
        fixed_defects = self._fetch_diff(fixed_json_url)
        return fixed_defects

    def status_failed(self):
        command = ['covscan', 'task-info', str(self.task_id)]
        process_output = Command(command, logger=self.module.logger).run()
        match = re.search('state_label = (.*)\n', process_output.stdout)

        if match is None:
            return True

        return match.group(1) == 'FAILED'

    # download added.html and fixed.html to keep them as build artifacts
    def download_artifacts(self):
        urlgrab(self.url + 'log/added.html?format=raw')
        urlgrab(self.url + 'log/fixed.html?format=raw')


class CICovscan(gluetool.Module):
    """
    CI Covscan module

    This module schedules a Covscan task, waits until it is finished and reports
    results in results shared function.

    config-map
    ==========

    .. code-block:: yaml

        ---

        - '(?:rhel|RHEL)-([67]).[0-9]+(?:-z)?-candidate|rhel-(7).1-ppc64le(?:-z)?-candidate':
            - 'rhel-\1-x86_64'
            - 'rhel-\1-x86_64-basescan'
    """

    name = 'covscan'
    description = 'Run covscan'
    supported_dryrun_level = DryRunLevels.DRY
    task = None

    options = {
        'blacklist': {
            'help': 'A comma separated list of blacklisted package names'
        },
        'task-id': {
            'help': 'Do not schedule Covscan task, just report from given task id',
        },
        'target_pattern': {
            'help': 'A comma separated list of regexes, which define enabled targets'
        },
        'config-map': {
            'help': 'Path to a file with ``target`` => ``target_config``, ``baseline_config`` patterns.',
            'metavar': 'FILE'
        }
    }

    shared_functions = ('covscan_xunit_serialize',)

    def sanity(self):
        check_for_commands(REQUIRED_CMDS)

    def version_diff_build(self, target, baseline, config, base_config):
        handle, task_id_filename = tempfile.mkstemp()
        try:
            os.close(handle)

            command = ['covscan', 'version-diff-build',
                       '--config', config,
                       '--base-config', base_config,
                       '--srpm', target,
                       '--base-srpm', baseline,
                       '--task-id-file', task_id_filename]

            try:
                Command(command, logger=self.logger).run()
            except GlueCommandError as exc:
                raise GlueError("Failure during 'covscan' execution: {}".format(exc.output.stderr))

            with open(task_id_filename, 'r') as task_id_file:
                covscan_task_id = int(task_id_file.readline())
        finally:
            _unlink(task_id_filename)
        return CovscanResult(self, covscan_task_id)

    def covscan_xunit_serialize(self, test_suite, result):
        # pylint: disable=no-self-use
        test_case = new_xml_element('testcase', _parent=test_suite, name=self.shared('primary_task').nvr,
                                    added=result.added, fixed=result.fixed, baseline=result.baseline,
                                    overall_result=result.overall_result, result_class=result.result_class,
                                    test_type=result.test_type)

        logs = new_xml_element('logs', _parent=test_case)

        def _log_url(log_name):
            return '{}/log/{}'.format(result.urls.get('covscan_url'), log_name)

        logs_url = result.urls.get('covscan_url') + 'log/{}'

        for log_type in ['added', 'fixed']:
            for log_ext in ['err', 'html', 'js']:
                log_name = "{}.{}".format(log_type, log_ext)
                new_xml_element("log", _parent=logs, name=log_name, url=_log_url(log_name))

        new_xml_element('log', _parent=logs, name='src.rpm', url=logs_url.format(result.baseline + '.src.rpm'))
        new_xml_element('log', _parent=logs, name='tar.gz', url=logs_url.format(result.baseline + '.tar.gz'))

        return test_suite

    def scan(self):
        covscan_result = None

        task_id = self.option('task-id')
        if task_id:
            self.info('Skipping covscan testing, using existing Covscan task id {}'.format(task_id))
            covscan_result = CovscanResult(self, task_id)

        if not covscan_result and not self.dryrun_allows('Run covscan testing'):
            raise GlueError('Can not run covscan dryrun without task-id parameter')

        if not covscan_result:
            target = self.task
            baseline = self.task.latest_released

            if not baseline:
                raise NoCovscanBaselineFoundError()

            self.info("Using '{}' (build task id: {}) as target".format(target.nvr, target.id))
            self.info("Using '{}' (build task id: {}) as baseline".format(baseline.nvr, baseline.id))

            log_dict(self.info, 'Obtaining source RPMs', {
                'target': target.srcrpm_url,
                'baseline': "{} -> baseline.src.rpm".format(target.srcrpm_url)
            })
            target_srpm = urlgrab(target.srcrpm_url)
            baseline_srpm = urlgrab(baseline.srcrpm_url, filename='baseline.src.rpm')

            self.info('Looking for covscan configuration in {}'.format(self.option('config-map')))
            configs = PatternMap(self.option('config-map'), logger=self.logger).match(self.task.target, multiple=True)

            if len(configs) != 2:
                raise GlueError('Mapping file does not provide exactly two configurations for this target')

            target_config = configs[0]
            baseline_config = configs[1]

            self.info('Issuing Covscan request')

            try:
                covscan_result = self.version_diff_build(target_srpm, baseline_srpm, target_config, baseline_config)
            finally:
                self.debug('Removing the downloaded source RPM')

                _unlink(target_srpm)
                _unlink(baseline_srpm)

        self.info('Covscan task url: {0}'.format(covscan_result.url))

        if covscan_result.status_failed():
            raise CovscanFailedError(covscan_result.url)

        covscan_result.download_artifacts()

        if covscan_result.added:
            self.info('FAILED: New defects in package.')
            overall_result = 'FAILED'

        else:
            self.info('PASSED: No new defects in package.')
            overall_result = 'PASSED'

        # Log in format expected by postbuild scripting
        self.info('Result of testing: {}'.format(overall_result))

        publish_result(self, CovscanTestResult, overall_result, covscan_result, self.task)

    def execute(self):
        self.require_shared('primary_task')

        self.task = self.shared('primary_task')

        blacklist = self.option('blacklist')
        if blacklist is not None:
            self.verbose('blacklisted packages: {}'.format(blacklist))
            if self.task.component in [splitted.strip() for splitted in blacklist.split(',')]:
                self.info('Package {} is blacklisted, skipping job'.format(self.task.component))
                return

        target = self.task.target
        enabled_targets = self.option('target_pattern')
        self.verbose('enabled targets: {}'.format(enabled_targets))

        if enabled_targets and any((re.compile(regex.strip()).match(target) for regex in enabled_targets.split(','))):
            self.info('Running covscan for {} on {}'.format(self.task.component, target))
            self.scan()
        else:
            self.info('Target {} is not enabled, skipping job'.format(target))
