import gluetool
from gluetool.utils import cached_property, dict_update
import libci.dispatch_job


class BeakerJob(libci.dispatch_job.DispatchJenkinsJobMixin, gluetool.Module):
    """
    Jenkins job module dispatching Beaker-based testing pipeline, as defined in ``ci-beaker.yaml`` file.

    .. note::

       Value of the ``--id`` option is, by default, first searched in the environment, at it is expected
       to be set by Jenkins' machinery, e.g. by the ``redhat-ci-plugin``.

    .. note::

       This module dispatches a Jenkins job, therefore it requires other module to provide connection
       to a Jenkins instance via the shared function ``jenkins``.
    """

    name = 'beaker-job'
    description = 'Job module dispatching Beaker-based testing pipeline.'

    job_name = 'ci-beaker'

    options = dict_update({}, libci.dispatch_job.DispatchJenkinsJobMixin.options, {
        'build-dependencies-options': {
            'help': 'Additional options for ``build-dependencies-options`` module.'
        },
        'guess-product-options': {
            'help': 'Additional options for ``guess-product`` module.'
        },
        'guess-distro-options': {
            'help': 'Additional options for ``guess-distro`` module.'
        },
        'wow-options': {
            'help': 'Additional options for ``workflow-tomorrow``.'
        },
        'jobwatch-options': {
            'help': 'Additional options for ``beaker-jobwatch``.'
        },
        'beaker-options': {
            'help': 'Additional options for ``beaker`` module.',
            'default': ''
        },

        # following options passed to beaker module
        'install-rpms-blacklist': {
            # pylint: disable=line-too-long
            'help': 'Regexp pattern (compatible with ``egrep``) - when installing build, matching packages will not be installed.',
            'type': str,
            'default': ''
        },
        'install-method': {
            'help': 'Yum method to use for installation (default: ``update``).',
            'type': str,
            'default': 'update'
        }
    })

    required_options = ('wow-options',)

    @cached_property
    def build_params(self):
        beaker_options = self.option('beaker-options')
        install_rpms_blacklist = self.option('install-rpms-blacklist')
        install_method = self.option('install-method')

        if install_rpms_blacklist:
            beaker_options = '{} --install-rpms-blacklist={}'.format(beaker_options, install_rpms_blacklist)

        if install_method:
            beaker_options = '{} --install-method={}'.format(beaker_options, install_method)

        return dict_update(super(BeakerJob, self).build_params, {
            'build_dependencies_options': self.option('build-dependencies-options'),
            'guess_product_options': self.option('guess-product-options'),
            'guess_distro_options': self.option('guess-distro-options'),
            'wow_options': self.option('wow-options'),
            'jobwatch_options': self.option('jobwatch-options'),
            'beaker_options': beaker_options
        })
