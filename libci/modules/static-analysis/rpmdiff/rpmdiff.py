import json
import re
import time
from libci import Module
from libci import CIError, CICommandError
from libci import utils
from libci.utils import run_command


RPMDIFF_PASS_STATES = ["Passed", "Info", "Waived", "Needs inspection"]
REQUIRED_CMDS = ['rpmdiff-remote']


class CIRpmdiff(Module):
    """
    CI RPMdiff module

    This module schedules an RPMdiff run, waits until it is finished and reports
    results. The run is considered as passed if its overal score is one of:
    {}
    """.format(', '.join(RPMDIFF_PASS_STATES))

    name = 'rpmdiff'
    description = 'Run RPMdiff analysis or comparison'

    options = {
        'blacklist': {
            'help': 'A comma seaparted list of blacklisted package names',
        },
        'type': {
            'help': 'Test type: analysis or comparison',
            'choices': ['analysis', 'comparison'],
        },
        'url': {
            'help': 'RPMdiff Hub URL',
        }
    }
    required_options = ['type']

    brew_task = None
    check_interval = 60
    max_timeout = 3600 * 24
    rpmdiff_cmd = None

    def sanity(self):
        utils.check_for_commands(REQUIRED_CMDS)

    @staticmethod
    def _parse_run_id(string):
        # parse JSON once BZ#1405962 is fixed
        match = re.search(r"u'run_id': (\d+)", string)
        if not match:
            msg = "could not find rpmdiff run id in rpmdiff-remote output"
            raise CIError(msg)
        return match.group(1)

    @staticmethod
    def _parse_web_url(string):
        # parse JSON once BZ#1405962 is fixed
        match = re.search(r"u'web_url': u'([^'']*)", string)
        if not match:
            msg = "could not find rpmdiff web url in rpmdiff-remote output"
            raise CIError(msg)
        return match.group(1)

    @staticmethod
    def _run_command(command):
        try:
            return run_command(command)

        except CICommandError as exc:
            raise CIError("Failure during 'rpmdiff-remote' execution: {}".format(exc.output.stderr))

    def _get_runinfo(self, task_id):
        command = self.rpmdiff_cmd + ["runinfo", task_id]

        return json.loads(CIRpmdiff._run_command(command).stdout)

    def _wait_until_finished(self, task_id):
        start_time = time.time()
        runinfo = self._get_runinfo(task_id)
        self.verbose("RPMdiff task [{}] state: {}".format(task_id, runinfo['overall_score']['description']))
        while runinfo['overall_score']['description'] in ['Running', 'Queued for test']:
            if (time.time() - start_time) > self.max_timeout:
                raise CIError("Waiting for RPMdiff results timed out ")
            time.sleep(self.check_interval)
            runinfo = self._get_runinfo(task_id)
        return runinfo

    def _run_rpmdiff(self, test_type, nvr_baseline=None):
        if self.brew_task.scratch:
            command = self.rpmdiff_cmd + ["schedule", str(self.brew_task.task_id)]
        else:
            command = self.rpmdiff_cmd + ["schedule", self.brew_task.nvr]

        if test_type == 'comparison':
            command += ["--baseline", nvr_baseline]

        out = CIRpmdiff._run_command(command).stdout
        # once we have valid JSON, we can parse taht here
        self.info("web url: {}".format(self._parse_web_url(out)))
        run_id = self._parse_run_id(out)
        return self._wait_until_finished(run_id)

    def execute(self):
        test_type = self.option('type')
        blacklist = self.option('blacklist')
        url = self.option('url')

        # override url if requested
        self.rpmdiff_cmd = ['rpmdiff-remote']
        if url:
            self.rpmdiff_cmd += ['--hub-url', url]

        # get a brew task instance
        self.brew_task = self.shared('brew_task')
        if self.brew_task is None:
            raise CIError('no brew build found, did you run brew module?')

        # blacklist packages
        if blacklist is not None:
            self.verbose('blacklisted packages: {}'.format(blacklist))
            if self.brew_task.component in blacklist.split(','):
                self.info('skipping blacklisted package {}'.format(self.brew_task.component))
                return

        comparison_msg = ''
        if test_type == 'comparison':
            if self.brew_task.latest is None:
                raise CIError('could not find baseline for this build')
            comparison_msg = 'compared to {}'.format(self.brew_task.latest)

        self.info("running {} for task '{}'{}".format(test_type, self.brew_task.task_id, comparison_msg))

        runinfo = self._run_rpmdiff(test_type, self.brew_task.latest)
        if runinfo['overall_score']['description'] in RPMDIFF_PASS_STATES:
            result = 'Passed'
        else:
            result = 'Failed'
        self.info('result: {}'.format(result))
