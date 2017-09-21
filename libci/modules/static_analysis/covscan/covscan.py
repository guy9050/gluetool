import os
import re
import tempfile
import json
from urllib2 import urlopen
from urlgrabber.grabber import urlgrab
import libci
from libci import Module, CIError, SoftCIError
from libci.ci import DryRunLevels
from libci.log import log_blob, format_dict
from libci.utils import cached_property, run_command, check_for_commands, CICommandError, dict_update
from libci.results import TestResult, publish_result

REQUIRED_CMDS = ['covscan']


class CovscanFailedError(SoftCIError):
    SUBJECT = 'Failed to test {nvr}'
    MODULE_NAME = 'covscan'
    BODY = """
CI aborted while trying to test the build via Covscan.
This is usually caused by:

* build via Covscan failed
* Covscan failed to finish testing for some reason

See Covscan logs for more details {covscan_result_url}.

If you have any questions, feel free to ask at Red Hat IRC channel #coverity or coverity-users@redhat.com
    """

    def __init__(self, url, task):
        super(CovscanFailedError, self).__init__('Covscan testing failed, task did not pass')

        self.url = url
        self.task = task

    def _template_variables(self):
        variables = super(CovscanFailedError, self)._template_variables()

        variables.update({
            'covscan_result_url': self.url,
            'nvr': self.task.nvr
        })

        return variables


class NoCovscanBaselineFoundError(SoftCIError):
    STATUS = 'SKIP'
    MODULE_NAME = 'covscan'
    SUBJECT = 'Could not find baseline package for Covscan'
    BODY = """
CI skipped the testing due to the fact, that the baseline build for Covscan was not found.
This can be caused by:

    * this is the first build of the package on this build target
    * there is some issue with build propagation for the build target

To check the tagged packages for given brew build you can user the 'brew' tool (e.g. for build
target 'rhel-7.4-candidate' and package bash):

    $ brew list-tagged rhel-7.4-candidate bash

Please file an issue to release enginnering if you encounter inconsistencies in Brew by sending
out an email to 'release-engineering@redhat.com'.

If you have any questions, feel free to ask at Red Hat IRC channel #coverity or coverity-users@redhat.com
    """

    def __init__(self):
        super(NoCovscanBaselineFoundError, self).__init__('Could not find baseline for this build')


class CovscanTestResult(TestResult):
    # pylint: disable=too-few-public-methods

    def __init__(self, ci, overall_result, covscan_result, task, **kwargs):
        urls = {
            'covscan_url': covscan_result.url,
            'brew_url': task.url
        }

        super(CovscanTestResult, self).__init__(ci, 'covscan', overall_result, urls=urls, **kwargs)

        self.fixed = len(covscan_result.fixed)
        self.added = len(covscan_result.added)
        self.baseline = task.latest

    def _serialize_to_json(self):
        serialized = super(CovscanTestResult, self)._serialize_to_json()
        return dict_update(serialized, {'baseline': self.baseline})

    def _serialize_to_xunit_property_dict(self, parent, properties, names):
        if 'covscan_url' in properties:
            libci.utils.new_xml_element('property', parent, name='baseosci.url.covscan-run',
                                        value=properties.pop('covscan_url'))

        if 'brew_url' in properties:
            # just drop this one - it can be reconstructed from task ID anyway
            properties.pop('brew_url')

        super(CovscanTestResult, self)._serialize_to_xunit_property_dict(parent, properties, names)


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
            raise CovscanFailedError(url, self.module.task)
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
        command = ['covscan', 'task-info', self.task_id]
        process_output = run_command(command)
        match = re.search('state_label = (.*)\n', process_output.stdout)

        if match is None:
            return True

        return match.group(1) == 'FAILED'

    # download added.html and fixed.html to keep them as build artifacts
    def download_artifacts(self):
        urlgrab(self.url + 'log/added.html?format=raw')
        urlgrab(self.url + 'log/fixed.html?format=raw')


class CICovscan(Module):
    """
    CI Covscan module

    This module schedules a Covscan task, waits until it is finished and reports
    results in results shared function.
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
        }
    }

    def sanity(self):
        check_for_commands(REQUIRED_CMDS)

    def version_diff_build(self, srpm, baseline, config, baseconfig):
        handle, task_id_filename = tempfile.mkstemp()
        try:
            os.close(handle)

            command = ['covscan', 'version-diff-build', '--config', config, '--base-config', baseconfig,
                       '--base-brew-build', baseline, '--srpm', srpm, '--task-id-file', task_id_filename]

            try:
                run_command(command)
            except CICommandError as exc:
                raise CIError("Failure during 'covscan' execution: {}".format(exc.output.stderr))

            with open(task_id_filename, 'r') as task_id_file:
                covscan_task_id = int(task_id_file.readline())
        finally:
            os.unlink(task_id_filename)
        return CovscanResult(self, covscan_task_id)

    def scan(self):
        covscan_result = None

        task_id = self.option('task-id')
        if task_id:
            self.info('Skipping covscan testing, using existing Covscan task id {}'.format(task_id))
            covscan_result = CovscanResult(self, task_id)

        if not covscan_result and not self.dryrun_allows('Run covscan testing'):
            raise CIError('Can not run covscan dryrun without task-id parameter')

        if not covscan_result:
            baseline = self.task.latest

            if not baseline:
                raise NoCovscanBaselineFoundError()

            self.info("Using latest non-scratch build '{}' as baseline".format(baseline))

            self.info('Obtaining source RPM from Brew build')
            srcrpm = urlgrab(self.task.srcrpm)

            self.info('Issuing Covscan request')

            config = 'rhel-{0}-x86_64'.format(self.task.rhel)
            base_config = 'rhel-{0}-x86_64-basescan'.format(self.task.rhel)

            try:
                covscan_result = self.version_diff_build(srcrpm, baseline, config, base_config)
            finally:
                self.debug('Removing the downloaded source RPM')
                os.unlink(srcrpm)

        self.info('Covscan task url: {0}'.format(covscan_result.url))

        if covscan_result.status_failed():
            raise CovscanFailedError(covscan_result.url, self.task)

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

        # get a brew task instance
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
