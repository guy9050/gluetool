from libci import Module
from libci.dispatch_job import DispatchJenkinsJobMixin
from libci.utils import cached_property, dict_update


class BeakerJob(DispatchJenkinsJobMixin, Module):
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
        return dict_update(super(BeakerJob, self).build_params, {
            'build_dependencies_options': self.option('build-dependencies-options'),
            'guess_product_options': self.option('guess-product-options'),
            'guess_distro_options': self.option('guess-distro-options'),
            'wow_options': self.option('wow-options'),
            'jobwatch_options': self.option('jobwatch-options')
        })
