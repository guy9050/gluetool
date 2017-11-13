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
        'build-dependencies-options': {
            'help': 'Additional options for ``build-dependencies-options`` module.'
        },
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
        'openstack-options': {
            'help': 'Additional options for openstack module.',
        },
        'restraint-scheduler-options': {
            'help': 'Additional options for ``restraint-scheduler`` module.',
            'default': ''
        },
        'restraint-runner-options': {
            'help': 'Additional options for restraint-runner module.'
        },

        # following options are passed to restraint-scheduler module
        'install-rpms-blacklist': {
            # pylint: disable=line-too-long
            'help': 'Regexp pattern (compatible with ``egrep``) - when installing build, matching packages will not be installed.',
            'type': str,
            'default': ''
        },
        'install-method': {
            'help': 'Yum method to use for installation (default: ``install``).',
            'type': str,
            'default': 'install'
        }
    })

    required_options = ('wow-options',)

    @cached_property
    def build_params(self):
        restraint_scheduler_options = self.option('restraint-scheduler-options')
        install_rpms_blacklist = self.option('install-rpms-blacklist')
        install_method = self.option('install-method')

        if install_rpms_blacklist:
            restraint_scheduler_options = '{} --install-rpms-blacklist="{}"'.format(restraint_scheduler_options,
                                                                                    install_rpms_blacklist)

        if install_method:
            restraint_scheduler_options = '{} --install-method={}'.format(restraint_scheduler_options, install_method)

        return dict_update(super(OpenStackJob, self).build_params, {
            'build_dependencies_options': self.option('build-dependencies-options'),
            'guess_product_options': self.option('guess-product-options'),
            'guess_beaker_distro_options': self.option('guess-beaker-distro-options'),
            'guess_openstack_image_options': self.option('guess-openstack-image-options'),
            'wow_options': self.option('wow-options'),
            'openstack_options': self.option('openstack-options'),
            'restraint_scheduler_options': restraint_scheduler_options,
            'restraint_runner_options': self.option('restraint-runner-options')
        })
