import os
from libci import Module
from libci import CIError, CICommandError
from libci import utils

from libci.utils import run_command

# Jenkins Job Builder YAML
JOB_NAME = 'ci-brew-dispatcher'
JJB_YAML = 'ci-brew-dispatcher.yaml'

REQUIRED_CMDS = ['jenkins-jobs']


class CIBrewDispatcherJob(Module):
    """
This module provides a Jenkins job for the ci-brew-dispatcher. It is intended
to create or update the job.

The jenkins job itself is defined via a Jenkins Job Builder yaml
file '{}' provided in the module's data directory.

The job trigger is the Redhat CI Plugin [1] with the following JMS Selector:

    CI_TYPE = 'brew-taskstatechange' AND method = 'build'

Requirements:
This module requires an available Jenkins connection via 'jenkins' module.
""".format(JJB_YAML)

    name = 'brew-dispatcher-job'
    description = 'Create/Update ci-brew-dispatcher Jenkins job'

    def sanity(self):
        # check for jjb
        utils.check_for_commands(REQUIRED_CMDS)

        # check if jjb yaml exists
        self.yaml = os.path.join(self.data_path, JJB_YAML)
        if not os.path.exists(self.yaml):
            raise CIError('job yaml not found in \'{}\''.format(self.yaml))

    def execute(self):
        jenkins = self.shared('jenkins')
        if jenkins is None:
            raise CIError('no jenkins connection found')

        try:
            run_command(['jenkins-jobs', 'update', self.data_path])

        except CICommandError as exc:
            raise CIError("Failure during 'jenkins-jobs' execution:\n{}".format(exc.output.stderr))

        self.info('created/updated JJB jobs from \'{}\''.format(self.data_path))