import gluetool
from gluetool.utils import cached_property, dict_update
import libci.dispatch_job


DEFAULT_WOW_OPTIONS_SEPARATOR = '#-#-#-#-#'


class OpenStackJob(libci.dispatch_job.DispatchJenkinsJobMixin, gluetool.Module):
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

    # DispatchJenkinsJobMixin.options contain hard defaults
    # pylint: disable=gluetool-option-hard-default
    options = dict_update({}, libci.dispatch_job.DispatchJenkinsJobMixin.options, {
        'build-dependencies-options': {
            'help': 'Additional options for ``build-dependencies-options`` module.'
        },
        'dist-git-options': {
            'help': 'Additional options for ``dist-git`` module.'
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
        'install-mbs-build-options': {
            'help': 'Additional options for install-mbs-build or install-mbs-build-execute module.'
        },
        'wow-options': {
            'help': 'Additional options for workflow-tomorrow.',
            'action': 'append',
            'default': []
        },
        'openstack-options': {
            'help': 'Additional options for openstack module.',
        },
        'brew-build-task-params-options': {
            'help': 'Additional options for ``brew-build-task-params`` module (default: %(default)s).',
            'default': ''
        },
        'test-schedule-runner-restraint-options': {
            'help': 'Additional options for test-schedule-runner-restraint module.'
        },
        'wow-options-separator': {
            'help': """
                    Due to technical limitations of Jenkins, when jobs want to pass multiple ``--wow-options``
                    instances to this module, it is necessary to encode them into a single string. To tell them
                    apart, this SEPARATOR string is used (default: %(default)s).
                    """,
            'metavar': 'SEPARATOR',
            'type': str,
            'action': 'store',
            'default': DEFAULT_WOW_OPTIONS_SEPARATOR
        },

        # following options are passed to brew-build-task-params module
        'install-rpms-blacklist': {
            # pylint: disable=line-too-long
            'help': """
                    Regexp pattern (compatible with ``egrep``) - when installing build, matching packages will
                    **not** be installed (default: %(default)s).
                    """,
            'type': str,
            'default': ''
        },
        'install-method': {
            'help': 'Yum method to use for installation (default: %(default)s).',
            'type': str,
            'default': 'multi'
        }
    })

    @cached_property
    def build_params(self):
        brew_build_task_params_options = self.option('brew-build-task-params-options')
        install_rpms_blacklist = self.option('install-rpms-blacklist')
        install_method = self.option('install-method')

        if install_rpms_blacklist:
            brew_build_task_params_options = '{} --install-rpms-blacklist={}'.format(brew_build_task_params_options,
                                                                                     install_rpms_blacklist)

        if install_method:
            brew_build_task_params_options = '{} --install-method={}'.format(brew_build_task_params_options,
                                                                             install_method)

        wow_options = self.option('wow-options-separator').join(self.option('wow-options'))

        return dict_update(super(OpenStackJob, self).build_params, {
            'build_dependencies_options': self.option('build-dependencies-options'),
            'dist_git_options': self.option('dist-git-options'),
            'guess_product_options': self.option('guess-product-options'),
            'guess_beaker_distro_options': self.option('guess-beaker-distro-options'),
            'guess_openstack_image_options': self.option('guess-openstack-image-options'),
            'install_mbs_build_options': self.option('install-mbs-build-options'),
            'wow_options': wow_options,
            'openstack_options': self.option('openstack-options'),
            'brew_build_task_params_options': brew_build_task_params_options,
            'test_schedule_runner_restraint_options': self.option('test-schedule-runner-restraint-options')
        })
