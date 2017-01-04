import kerberos
import re
import requests
import subprocess
import time
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from libci import Module
from libci import libciError
from libci import utils

RPMDIFF_URL = "https://rpmdiff.engineering.redhat.com"
RPMDIFF_BASELINE_URL = "http://rpmdiff-baseline.usersys.redhat.com"
RPMDIFF_PASS_STATES = ["Passed", "Info", "Waived", "Needs inspection"]
RPMDIFF_KERBEROS_PRINCIPAL = "HTTP@rpmdiff-web-01.host.prod.eng.bos.redhat.com"

REQUIRED_CMDS = ['rpmdiff-remote']


class RPMDiffTask(object):
    def __init__(self, rpmdiff_task_id):
        self.rpmdiff_url = RPMDIFF_URL
        self.api_url = self.rpmdiff_url + "/json/"
        self.login_url = self.rpmdiff_url + "/auth/login/?next=/"
        self.task_id = int(rpmdiff_task_id)
        self._json_data = None
        self.url = self.rpmdiff_url + "/run/" + str(self.task_id)

    @staticmethod
    def get_kerberos_ticket():
        _, krb_context = kerberos.authGSSClientInit(RPMDIFF_KERBEROS_PRINCIPAL)
        kerberos.authGSSClientStep(krb_context, "")
        return kerberos.authGSSClientResponse(krb_context)

    def _fetch_json(self):
        payload = {
            "id": "jsonrpc",
            "jsonrpc": "1.0",
            "method": "getResult",
            "params": [self.task_id]
        }
        headers = {
            "Authorization": "Negotiate " + self.get_kerberos_ticket()
        }
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
        request = requests.post(self.api_url, json=payload, headers=headers,
                                verify=False)
        if request.status_code != 200:
            msg = "Http status code {} ".format(request.status_code)
            msg += "in 'rpmdiff' execution"
            raise libciError("Http status code %s in 'rpmdiff' execution" % request.status_code)
        self._json_data = request.json()

    def _json(self):
        if self._json_data is None:
            self._fetch_json()
        return self._json_data

    @property
    def state(self):
        return self._json()['result']['state']

    @property
    def result(self):
        return self._json()['result']['result']

    @property
    def data(self):
        return self._json()['result']['data']

    @property
    def error(self):
        if "error" not in self._json()['result']:
            return ""
        return self._json()['result']['error']

    @property
    def finished(self):
        return self.state == "CLOSED"

    @property
    def passed(self):
        return self.finished and self.result in RPMDIFF_PASS_STATES


class CIRpmdiff(Module):
    """
CI RPMdiff module

This module schedules an RPMdiff comparison waits until it is finished.
"""

    name = 'rpmdiff'
    description = 'Run RPMdiff analysis or comparison'

    options = {
        'task-id': {
            'help': 'Brew task id',
            'type': int,
        },
        'type': {
            'help': 'Test type: analysis or comparison',
            'choices': ['analysis', 'comparison'],
        }
    }
    required_options = ['task-id', 'type']

    brew_task = None
    check_interval = 60
    max_timeout = 3600 * 24

    def sanity(self):
        utils.check_for_commands(REQUIRED_CMDS)

    @staticmethod
    def _parse_task_id(string, is_scratch):
        # TODO parse JSON once BZ#1405962 is fixed
        match = re.search(r"u'run_id': (\d+)", string)
        if not match:
            msg = "could not find rpmdiff run id in rpmdiff-remote output"
            raise libciError(msg)
        return match.group(1)

    @staticmethod
    def _parse_web_url(string):
        # TODO parse JSON once BZ#1405962 is fixed
        match = re.search(r"u'web_url': u'([^'']*)", string)
        if not match:
            msg = "could not find rpmdiff web url in rpmdiff-remote output"
            raise libciError(msg)
        return match.group(1)

    def _wait_until_finished(self, task_id):
        start_time = time.time()
        task = RPMDiffTask(task_id)
        self.verbose("RPMdiff task [{}] state: {}".format(task_id, task.state))
        while not task.finished:
            task = RPMDiffTask(task_id)
            if (time.time() - start_time) > self.max_timeout:
                raise libciError("Timeout of 'rpmdiff' execution")
            time.sleep(self.check_interval)

    def _comparison_command(self, nvr_baseline):
        if self.brew_task.scratch:
            return ["rpmdiff-remote", "schedule", str(self.brew_task.task_id),
                    "--baseline", nvr_baseline]
        else:
            return ["rpmdiff-remote", "schedule", self.brew_task.nvr,
                    "--baseline", nvr_baseline]

    def _analysis_command(self):
        if self.brew_task.scratch:
            return ["rpmdiff-remote", "schedule", str(self.brew_task.task_id)]
        else:
            return ["rpmdiff-remote", "schedule", self.brew_task.nvr]

    def _run_command(self, command, wait_until_finished=True):
        msg = "schedule command: {}".format(subprocess.list2cmdline(command))
        self.verbose(msg)
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        p_status = process.wait()
        (p_out, p_err) = process.communicate()
        self.debug("stdout: {}".format(p_out or "no output"))
        self.debug("stderr: {}".format(p_err or "no output"))
        if p_status > 0:
            msg = "Failure during 'rpmdiff' command execution, return code: %s"
            raise libciError(msg)
        task_id = self._parse_task_id(p_out, self.brew_task.scratch)
        self.info("web url: {}".format(self._parse_web_url(p_out)))
        if wait_until_finished:
            self._wait_until_finished(task_id)
        return task_id

    def _run_comparison(self, nvr_baseline, wait_until_finished=True):
        command = self._comparison_command(nvr_baseline)
        task_id = self._run_command(command, wait_until_finished)
        return RPMDiffTask(task_id)

    def _run_analysis(self, wait_until_finished=True):
        command = self._analysis_command()
        task_id = self._run_command(command, wait_until_finished)
        return RPMDiffTask(task_id)

    def execute(self):
        task_id = self.option('task-id')
        test_type = self.option('type')

        # get a brew task instance
        self.brew_task = self.shared('brew_task', task_id)
        if not self.brew_task:
            raise libciError('no brew connection found')
        target = self.brew_task.target.target

        if test_type == 'analysis':
            msg = 'running {} for '.format(test_type)
            if self.brew_task.scratch is True:
                msg += 'scratch '
            msg += 'build of \'{}\' '.format(self.brew_task.nvr)
            msg += 'with build-target \'{}\''.format(target)
            self.info(msg)
            result = self._run_analysis()
        else:
            latest = self.brew_task.latest
            if not latest:
                raise libciError('could not find baseline for this build')
            msg = 'running {} for '.format(test_type)
            if self.brew_task.scratch is True:
                msg += 'scratch '
            msg += 'build of \'{}\' '.format(self.brew_task.nvr)
            msg += 'compared to \'{}\' '.format(latest)
            msg += 'with build target \'{}\''.format(target)
            self.info(msg)
            result = self._run_comparison(latest)
        self.info('result: {}'.format(result.result))
