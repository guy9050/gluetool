from libci import Module
from libci.dispatch_job import DispatchJenkinsJobMixin
from libci.utils import cached_property, dict_update


class BeakerFedoraJob(DispatchJenkinsJobMixin, Module):
    """
    Jenkins job module dispatching Beaker based testing pipeline for Fedora, as defined
    in ``ci-beaker-fedora.yaml`` file.

    .. note::

       Value of the ``id`` parameter is read from the shared function ``primary_task``.

    .. note::

       This module dispatches a Jenkins job, therefore it requires other module to provide connection
       to a Jenkins instance via the shared function ``jenkins``.
    """

    name = 'beaker-fedora-job'
    description = 'Job module dispatching Beaker-based testing pipeline.'

    job_name = 'ci-beaker-fedora'

    options = dict_update({}, DispatchJenkinsJobMixin.options, {
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
        }
    })

    required_options = ('wow-options',)

    @cached_property
    def build_params(self):
        return dict_update(super(BeakerFedoraJob, self).build_params, {
            'build_dependencies_options': self.option('build-dependencies-options'),
            'guess_product_options': self.option('guess-product-options'),
            'guess_distro_options': self.option('guess-distro-options'),
            'wow_options': self.option('wow-options'),
            'jobwatch_options': self.option('jobwatch-options')
        })
