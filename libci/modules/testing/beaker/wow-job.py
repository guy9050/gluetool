import os
from libci import Module
from libci import CIError
from libci import utils
from libci.utils import run_command
from jenkinsapi.custom_exceptions import UnknownJob

# Jenkins Job Builder YAML
JOB_NAME = 'ci-wow'
JJB_YAML = JOB_NAME + '.yaml'

# required cmdline tools
REQUIRED_CMDS = ['jenkins-jobs']


class CIWowJob(Module):
    """
CI bkr workflow-tomorrow job module

This module provides a Jenkins job for testing via bkr workflow-tomorrow.
It is able to create these jobs and also execute it. By default it reads the
Brew task id from the environment. It is expected to call this module from a
job, where id is exported by the redhat-ci-plugin. The id can be
also passed as an argument.

The jenkins job is defined via a Jenkins Job Builder yaml file:
    ci-wow

Important note:
This module requires an available Jenkins connection - via the jenkins module.
"""

    name = 'wow-job'
    description = 'Create and run beaker workflow-tomorrow job'

    options = {
        'args': {
            'help': 'Additional parameters passed to wow',
        },
        'id': {
            'help': 'Brew Task ID (read also from the environment)',
        },
    }
    required_options = ['args']
    jenkins = None
    tid = None

    def sanity(self):
        # check for jjb
        utils.check_for_commands(REQUIRED_CMDS)

        # check for available id
        self.tid = self.option('id') or os.environ['id']
        if not self.tid:
            raise CIError('id not found in environment')

    def update_job(self):
        run_command(['jenkins-jobs', '--flush-cache', 'update', os.path.join(self.data_path, JJB_YAML)])
        self.jenkins = self.shared('jenkins', reconnect=True)

    def execute(self):
        self.jenkins = self.shared('jenkins')
        if self.jenkins is None:
            raise CIError('no jenkins connection found')

        try:
            self.jenkins[JOB_NAME]
        except UnknownJob:
            self.update_job()

        args = self.option('args')
        self.jenkins[JOB_NAME].invoke(build_params={'id': self.tid, 'args': args})
        self.info("invoked job '{}' with given parameters".format(JOB_NAME))
