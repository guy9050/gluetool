from libci import Module
from libci.dispatch_job import DispatchJenkinsJobMixin
from libci.utils import cached_property, dict_update


class OpenStackJob(DispatchJenkinsJobMixin, Module):
    """
    Jenkins job module dispatching OpenStack-based testing pipeline, as defined in ``ci-openstack.yaml`` file.

    .. note::

       Value of the ``--id`` option is, by default, first searched in the environment, at it is expected
       to be set by Jenkins' machinery, e.g. by the ``redhat-ci-plugin``.

    .. note::

       This module dispatches a Jenkins job, therefore it requires other module to provide connection
       to a Jenkins instance via the shared function ``jenkins``.
    """

    name = 'openstack-job'
    description = 'Run package tests using restraint and OpenStack guest'

    job_name = 'ci-openstack'

    options = dict_update({}, DispatchJenkinsJobMixin.options, {
        'guess-product-options': {
            'help': 'Additional options for ``guess-product`` module.'
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
        }
    })

    required_options = ('wow-options',)

    @cached_property
    def build_params(self):
        return dict_update(super(OpenStackJob, self).build_params, {
            'guess_product_options': self.option('guess-product-options'),
            'guess_beaker_distro_options': self.option('guess-beaker-distro-options'),
            'guess_openstack_image_options': self.option('guess-openstack-image-options'),
            'wow_options': self.option('wow-options'),
            'restraint_runner_options': self.option('restraint-runner-options'),
        })
