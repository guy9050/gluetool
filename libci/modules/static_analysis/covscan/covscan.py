import os
import re
import tempfile
import json
import StringIO
import gzip
from urllib2 import urlopen
from urlgrabber.grabber import urlgrab
from libci import Module, CIError, SoftCIError
from libci.utils import cached_property, log_blob, run_command, check_for_commands, format_dict, CICommandError
from libci.results import TestResult, publish_result

REQUIRED_CMDS = ['covscan']


class NoResultFilesError(SoftCIError):
    SUBJECT = 'Failed to fetch covscan result files'
    BODY = """
CI failed to download necessary files from Covscan site, therefore it cannot
decide what to do next. It may have been caused by failed covscan task. You
may find solution on {covscan_result_url}.
    """

    def __init__(self, result):
        super(NoResultFilesError, self).__init__('Failed to fetch Covscan files')

        self.result = result

    def _template_variables(self):
        variables = super(NoResultFilesError, self)._template_variables()

        variables.update({
            'covscan_result_url': self.result.url
        })

        return variables


class CovscanTestResult(TestResult):
    # pylint: disable=too-few-public-methods

    def __init__(self, overall_result, covscan_result, brew_task, **kwargs):
        urls = {
            'covscan_url': covscan_result.url,
            'brew_url': brew_task.url
        }

        super(CovscanTestResult, self).__init__('covscan', overall_result, urls=urls, **kwargs)

        self.fixed = len(covscan_result.fixed)
        self.added = len(covscan_result.added)
        self.baseline = brew_task.latest


class CovscanResult(object):
    def __init__(self, module, task_id):
        self.module = module
        self.task_id = task_id
        self.url = 'http://cov01.lab.eng.brq.redhat.com/covscanhub/task/{}/'.format(task_id)

    def _fetch_diff(self, url):
        diff_json = urlopen(url).read()
        diff = json.loads(diff_json)
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
        url = self.url + 'log/stdout.log?format=raw'
        response = urlopen(url)

        # response is gz archiv, it has to be decompressed
        compressed_file = StringIO.StringIO()
        compressed_file.write(response.read())
        compressed_file.seek(0)

        decompressed_file = gzip.GzipFile(fileobj=compressed_file, mode='rb')
        output = decompressed_file.read()

        log_blob(self.module.debug, 'fetched from {}'.format(url), output)
        return output == "Failing because of at least one subtask hasn't closed properly.\n"

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

    options = {
        'blacklist': {
            'help': 'A comma separated list of blacklisted package names'
        },
        'target_pattern': {
            'help': 'A comma separated list of regexes, which define enabled targets'
        }
    }

    brew_task = None

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
        baseline = self.brew_task.latest

        if not baseline:
            raise CIError('Covscan baseline not detected, skipping job')

        self.info('Using (second) latest non-scratch build [%s] in tag [%s] as baseline',
                  baseline, self.brew_task.target.destination_tag)

        self.info('Obtaining source RPM from Brew build')
        srcrpm = urlgrab(self.brew_task.srcrpm)

        self.info('Issuing Covscan request')

        config = 'rhel-{0}-x86_64'.format(self.brew_task.target.rhel)
        base_config = 'rhel-{0}-x86_64-basescan'.format(self.brew_task.target.rhel)

        try:
            covscan_result = self.version_diff_build(srcrpm, baseline, config, base_config)
        finally:
            self.debug('Removing the downloaded source RPM')
            os.unlink(srcrpm)

        self.info('Covscan task url: {0}'.format(covscan_result.url))

        if covscan_result.status_failed():
            raise NoResultFilesError(covscan_result)

        covscan_result.download_artifacts()

        if covscan_result.added:
            self.info('FAIL: New defects in package.')
            overall_result = 'FAIL'

        else:
            self.info('PASS: No new defects in package.')
            overall_result = 'PASS'

        # Log in format expected by postbuild scripting
        self.info('Result of testing: {}'.format(overall_result))

        publish_result(self, CovscanTestResult, overall_result, covscan_result, self.brew_task)

    def execute(self):
        # get a brew task instance
        self.brew_task = self.shared('brew_task')
        if self.brew_task is None:
            raise CIError('no brew build found, did you run brew module?')

        blacklist = self.option('blacklist')
        if blacklist is not None:
            self.verbose('blacklisted packages: {}'.format(blacklist))
            if self.brew_task.component in [splitted.strip() for splitted in blacklist.split(',')]:
                self.info('Skipping blacklisted package {}'.format(self.brew_task.component))
                return

        target = self.brew_task.target.target
        enabled_targets = self.option('target_pattern')
        self.verbose('enabled targets: {}'.format(enabled_targets))

        if enabled_targets and any((re.compile(regex.strip()).match(target) for regex in enabled_targets.split(','))):
            self.info('Running covscan for {} on {}'.format(self.brew_task.component, target))
            self.scan()
        else:
            self.info('Target {} is not enabled, skipping job'.format(target))
