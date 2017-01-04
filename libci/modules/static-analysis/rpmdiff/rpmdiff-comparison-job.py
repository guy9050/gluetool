import os
import subprocess
from libci import Module
from libci import libciError
from libci import utils

# Jenkins Job Builder YAML
JOB_NAME = 'ci-rpmdiff-comparison'
JJB_YAML = 'ci-rpmdiff-comparison.yaml'

REQUIRED_CMDS = ['jenkins-jobs']


class CIRpmdiffComparisonJob(Module):
    """
CI RPMdiff comparison job module

This module provides a Jenkins job for RPMdiff comparison testing. It is able to
create this job and also execute it. By default it reads the id from
the environment. It is expected to call this module from a job, where
id is exported by the redhat-ci-plugin. The id can be
also passed as an argument.

The jenkins job itself is defined via a Jenkins Job Builder yaml
file 'ci-rpmdiff-comparison.yaml' provided in the module's data directory.

Important note:
This module requires an available Jenkins connection - via the jenkins module.
"""

    name = 'rpmdiff-comparison-job'
    description = 'Create and run RPMdiff comparison job'

    options = {
        'id': {
            'help': 'Brew task id',
        },
    }

    def sanity(self):
        # check for jjb
        utils.check_for_commands(REQUIRED_CMDS)

        # check if jjb yaml exists
        self.yaml = os.path.join(self.data_path, JJB_YAML)
        if not os.path.exists(self.yaml):
            raise libciError('job yaml not found in \'{}\''.format(self.yaml))

        # check for available id
        self.id = self.option('id') or os.environ['id']
        if not self.id:
            raise libciError('id not found in environment')

    def execute(self):
        jenkins = self.shared('jenkins')
        if not jenkins:
            raise libciError('no jenkins connection found')

        out = subprocess.check_output(['jenkins-jobs', 'update',
                                       self.data_path],
                                      stderr=subprocess.STDOUT)
        # TODO parse JJB output and inform about the update
        self.debug(out)

        jenkins[JOB_NAME].invoke(self.id, build_params={
                                    'id': self.id
                                 })
        msg = 'invoked job \'{}\' with build params'.format(JOB_NAME)
        msg += 'with build params \'id={}\''.format(self.id)
        self.info(msg)
