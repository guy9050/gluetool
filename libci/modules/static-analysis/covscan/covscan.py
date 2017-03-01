import os
import re
import tempfile
import subprocess
import json
from urllib2 import urlopen
from urlgrabber.grabber import urlgrab
from libci import Module
from libci import utils
from libci import CIError

REQUIRED_CMDS = ['covscan']


class CovscanResult(object):
    def __init__(self, task_id):
        self.task_id = task_id
        self.url = 'http://cov01.lab.eng.brq.redhat.com/covscanhub/task/%s/' % task_id
        self._fixed_defects = None
        self._added_defects = None

    @staticmethod
    def _fetch_diff(url):
        diff_json = urlopen(url).read()
        diff = json.loads(diff_json)
        defects = diff['defects']
        return defects

    @property
    def added(self):
        if self._added_defects is None:
            added_json_url = self.url + 'log/added.js?format=raw'
            self._added_defects = self._fetch_diff(added_json_url)
        return self._added_defects

    @property
    def fixed(self):
        if self._fixed_defects is None:
            fixed_json_url = self.url + 'log/fixed.js?format=raw'
            self._fixed_defects = self._fetch_diff(fixed_json_url)
        return self._fixed_defects

    def status_failed(self):
        stdout = urlopen(self.url + 'log/stdout.log?format=raw')
        return stdout == "Failing because of at least one subtask hasn't closed properly."

    def download_artifacts(self):
        urlgrab(self.url + 'log/added.html?format=raw')
        urlgrab(self.url + 'log/fixed.html?format=raw')


class Covscan(object):
    def __init__(self, srpm, baseline, config, baseconfig):
        self.srpm = srpm
        self.baseline = baseline
        self.config = config
        self.baseconfig = baseconfig
        self.result_url = None

    def version_diff_build(self, module):
        handle, task_id_filename = tempfile.mkstemp()
        os.close(handle)

        command = ['covscan', 'version-diff-build', '--config', self.config, '--base-config', self.baseconfig,
                   '--base-brew-build', self.baseline, '--srpm', self.srpm, '--task-id-file', task_id_filename]

        module.verbose(' '.join(command))

        if subprocess.call(command) > 0:
            raise CIError("Failure during 'covscan' client execution")
        with open(task_id_filename, 'r') as task_id_file:
            covscan_task_id = int(task_id_file.readline())
        os.unlink(task_id_filename)
        return CovscanResult(covscan_task_id)


class CICovscan(Module):

    name = 'covscan'
    description = 'Run covscan'

    options = {
        'blacklist': {
            'help': 'A comma seaparted list of blacklisted package names'
        },
        'target_pattern': {
            'help': 'A comma seaparted list of regexes, which define enabled targets'
        }
    }

    shared_functions = ['results']

    brew_task = None
    _results = []

    def results(self):
        return self._results

    def sanity(self):
        utils.check_for_commands(REQUIRED_CMDS)

    def scan(self):
        brew = self.shared('get_brew')

        component = self.brew_task.component
        destination_tag = self.brew_task.target.destination_tag
        builds = brew.listTagged(destination_tag, None, True, latest=2, package=component)
        try:
            if self.brew_task.scratch:
                baseline = builds[0]['nvr']
                self.info('Using latest non-scratch build [%s] in tag [%s] as baseline',
                          baseline, self.brew_task.target.destination_tag)
            else:
                baseline = builds[1]['nvr']
                self.info('Using second latest non-scratch build [%s] in tag [%s] as baseline',
                          baseline, self.brew_task.target.destination_tag)
        except LookupError:
            self.info('Covscan baseline not detected, skipping job')
            return

        self.info('Obtaining source RPM from Brew build')
        srcrpm = self.brew_task.srcrpm
        srcrpm = urlgrab(srcrpm)

        self.info('Issuing Covscan request')

        config = 'rhel-{0}-x86_64'.format(self.brew_task.target.rhel)
        base_config = 'rhel-{0}-x86_64-basescan'.format(self.brew_task.target.rhel)

        covscan = Covscan(srcrpm, baseline, config, base_config)
        covscan_result = covscan.version_diff_build(self)

        if covscan_result.status_failed():
            raise CIError('Failed to get result files. Try find solution here: {0}'.format(covscan_result.url))

        self.debug('Removing the downloaded source RPM')
        os.unlink(srcrpm)

        covscan_result.download_artifacts()

        if len(covscan_result.added) > 0:
            self.info('FAIL: New defects in package.')
            overall_result = 'FAIL'

        else:
            self.info('PASS: No new defects in package.')
            overall_result = 'PASS'

        result = {
            'type': 'covscan',
            'result': overall_result,
            'urls': {
                'covscan_url': covscan_result.url
            }
        }

        if 'BUILD_URL' in os.environ:
            result['urls']['jenkins_job'] = os.environ['BUILD_URL']

        self._results = self.shared('results') or []
        self._results.append(result)

    def execute(self):
        # get a brew task instance
        self.brew_task = self.shared('brew_task')
        if self.brew_task is None:
            raise CIError('no brew build found, did you run brew module?')

        blacklist = self.option('blacklist')
        if blacklist is not None:
            self.verbose('blacklisted packages: {}'.format(blacklist))
            if self.brew_task.component in blacklist.split(','):
                self.info('Skipping blacklisted package {}'.format(self.brew_task.component))
                return

        target = self.brew_task.target.target
        scan = False
        enabled_targets = self.option('target_pattern')
        self.verbose('enabled targets: {}'.format(enabled_targets))
        for regex in enabled_targets.split(','):
            pattern = re.compile(regex.strip())
            if pattern.match(target):
                scan = True

        if scan:
            self.info('Running covscan for {} on {}'.format(self.brew_task.component, target))
            self.scan()
        else:
            self.info('Target {} is not enabled, skipping job'.format(target))
