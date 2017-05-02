import os
from libci import Module
from libci import CIError
from libci import utils

JOB_NAME = 'ci-openstack'


class CIOpenstackJob(Module):
    """
    CI module for testing packages using OpenStack guests and restraint harness.

    Expects brew task id on the input (either via command-line option or env variable).

    The Jenkins job is described in ci-openstack.yaml file.
    """

    name = 'openstack-job'
    description = 'Run package tests using restraint and OpenStack guest'

    options = {
        'id': {
            'help': 'Brew task id.',
        },
        'pipeline-prepend': {
            'help': 'citool options that will be added at the beginning of citool pipeline'
        },
        'pipeline-append': {
            'help': 'citool options that will be added at the end of citool pipeline'
        },
        'guess-beaker-distro-options': {
            'help': 'Additional options for guess-beaker-distro module.'
        },
        'guess-openstack-image-options': {
            'help': 'Additional options for guess-openstack-image module.'
        },
        'wow-options': {
            'help': 'Additional options for workflow-tomorrow.'
        },
        'restraint-runner-options': {
            'help': 'Additional options for restraint-runner module.'
        },
        'notify-recipients-options': {
            'help': 'Additional options for notify-recipients module'
        },
        'notify-email-options': {
            'help': 'Additional options for notify-email module'
        }
    }

    required_options = ['wow-options']

    _task_id = None

    def sanity(self):
        self._task_id = self.option('id') or os.environ.get('id', None)

        if not self._task_id:
            raise CIError('Brew task id not found')

    def execute(self):
        jenkins = self.shared('jenkins')
        if jenkins is None:
            raise CIError('No jenkins connection found')

        build_params = {
            'id': self._task_id,
            'pipeline_prepend': self.option('pipeline-prepend'),
            'pipeline_append': self.option('pipeline-append'),
            'guess_beaker_distro_options': self.option('guess-beaker-distro-options'),
            'guess_openstack_image_options': self.option('guess-openstack-image-options'),
            'wow_options': self.option('wow-options'),
            'restraint_runner_options': self.option('restraint-runner-options'),
            'notify_recipients_options': self.option('notify-recipients-options'),
            'notify_email_options': self.option('notify-email-options')
        }

        self.debug('build params:\n{}'.format(utils.format_dict(build_params)))

        jenkins[JOB_NAME].invoke(build_params=build_params)

        self.info("invoked job '{}' with given parameters".format(JOB_NAME))
