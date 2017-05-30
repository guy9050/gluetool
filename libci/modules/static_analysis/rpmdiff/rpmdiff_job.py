import os
from libci import Module
from libci import CIError
from libci import utils
from libci.utils import run_command
from jenkinsapi.custom_exceptions import UnknownJob


# Jenkins Job Builder YAML
CMP_JOB_NAME = 'ci-rpmdiff-comparison'
CMP_JJB_YAML = 'ci-rpmdiff-comparison.yaml'
ANL_JOB_NAME = 'ci-rpmdiff-analysis'
ANL_JJB_YAML = 'ci-rpmdiff-analysis.yaml'

# required cmdline tools
REQUIRED_CMDS = ['jenkins-jobs']


class CIRpmdiffJob(Module):
    """
CI RPMdiff job module

This module provides a Jenkins job for RPMdiff analysis and comparison testing.
It is able to create these jobs and also execute it. By default it reads the
Brew task id from the environment. It is expected to call this module from a
job, where id is exported by the redhat-ci-plugin. The id can be
also passed as an argument.

The jenkins jobs are defined via a Jenkins Job Builder yaml files:
    ci-rpmdiff-analysis.yaml
    ci-rpmdiff-comparison.yaml

Important note:
This module requires an available Jenkins connection - via the jenkins module.
"""

    name = 'rpmdiff-job'
    description = 'Create and run RPMdiff analysis or comparison job'

    options = {
        'id': {
            'help': 'Brew task id',
        },
        'type': {
            'help': 'Test type: analysis or comparison',
            'choices': ['analysis', 'comparison'],
        },
        'notify-recipients-options': {
            'help': 'Additional options for notify-recipients module',
            'action': 'append',
            'default': []
        }
    }
    required_options = ['type']
    jenkins = None

    def sanity(self):
        # check for jjb
        utils.check_for_commands(REQUIRED_CMDS)

        # check if jjb yaml exists
        if self.option('type') == 'comparison':
            self.job_name = CMP_JOB_NAME
            self.yaml = os.path.join(self.data_path, CMP_JJB_YAML)
        else:
            self.job_name = ANL_JOB_NAME
            self.yaml = os.path.join(self.data_path, ANL_JJB_YAML)
        if not os.path.exists(self.yaml):
            raise CIError('job yaml not found in \'{}\''.format(self.yaml))

        # check for available id
        self.tid = self.option('id') or os.environ['id']
        if not self.tid:
            raise CIError('id not found in environment')

    def update_job(self):
        run_command(['jenkins-jobs', '--flush-cache', 'update', self.yaml])

        # reconnect to jenkins
        self.jenkins = self.shared('jenkins', reconnect=True)

    def execute(self):
        self.jenkins = self.shared('jenkins')
        if self.jenkins is None:
            raise CIError('no jenkins connection found')

        notify_recipients_options = self.option('notify-recipients-options')
        if notify_recipients_options:
            notify_recipients_options = ' '.join(notify_recipients_options)

        else:
            notify_recipients_options = None

        try:
            self.jenkins[self.job_name]
        except UnknownJob:
            self.update_job()

        build_params = {
            'id': self.tid,
            'notify_recipients_options': notify_recipients_options
        }

        self.jenkins[self.job_name].invoke(build_params=build_params)

        self.info("invoked job '{}' with given parameters".format(self.job_name))
        self.debug("invoked job '{}' with parameters:\n{}".format(self.job_name, utils.format_dict(build_params)))
