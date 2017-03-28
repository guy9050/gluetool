import os
from libci import Module
from libci import CIError
from libci import utils

# Jenkins Job Builder YAML
JOB_NAME = 'ci-wow'

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
        'id': {
            'help': 'Brew Task ID (read also from the environment)',
        },
        'pipeline-prepend': {
            'help': 'citool options that will be added at the beginning of citool pipeline'
        },
        'pipeline-append': {
            'help': 'citool options that will be added at the end of citool pipeline'
        },
        'guess-distro-options': {
            'help': 'Additional options for guess-distro module'
        },
        'wow-options': {
            'help': 'Additional options for workflow-tomorrow'
        },
        'jobwatch-options': {
            'help': 'Additional options for beaker-jobwatch'
        },
        'notify-email-options': {
            'help': 'Additional options for notify-email module'
        }
    }

    required_options = ['wow-options']
    jenkins = None
    tid = None

    def sanity(self):
        # check for jjb
        utils.check_for_commands(REQUIRED_CMDS)

        # check for available id
        self.tid = self.option('id') or os.environ['id']
        if not self.tid:
            raise CIError('id not found in environment')

    def execute(self):
        self.jenkins = self.shared('jenkins')
        if self.jenkins is None:
            raise CIError('no jenkins connection found')

        build_params = {
            'id': self.tid,
            'pipeline_prepend': self.option('pipeline-prepend'),
            'pipeline_append': self.option('pipeline-append'),
            'guess_distro_options': self.option('guess-distro-options'),
            'wow_options': self.option('wow-options'),
            'jobwatch_options': self.option('jobwatch-options'),
            'notify_email_options': self.option('notify-email-options')
        }

        self.jenkins[JOB_NAME].invoke(build_params=build_params)

        self.info("invoked job '{}' with given parameters".format(JOB_NAME))
        self.debug("invoked job '{}' with parameters:\n{}".format(JOB_NAME, utils.format_dict(build_params)))
