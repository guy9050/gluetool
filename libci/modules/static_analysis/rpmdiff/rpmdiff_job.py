from libci import Module
from libci.dispatch_job import DispatchJenkinsJobMixin
from libci.utils import dict_update


# Jenkins Job Builder YAML
COMPARISON_JOB_NAME = 'ci-rpmdiff-comparison'
ANALYSIS_JOB_NAME = 'ci-rpmdiff-analysis'


class RpmdiffJob(DispatchJenkinsJobMixin, Module):
    """
    Jenkins job module dispatching RPMdiff analysis and comparison testing, as defined in ``ci-rpmdiff-analysis.yaml``
    and ``ci-rpmdiff-comparison.yaml`` files.

    .. note::

       Value of the ``--id`` option is, by default, first searched in the environment, at it is expected
       to be set by Jenkins' machinery, e.g. by the ``redhat-ci-plugin``.

    .. note::

       This module dispatches a Jenkins job, therefore it requires other module to provide connection
       to a Jenkins instance via the shared function ``jenkins``.
    """

    name = 'rpmdiff-job'
    description = 'Job module dispatching RPMdiff analysis and comparison pipeline.'

    options = dict_update({}, DispatchJenkinsJobMixin.options, {
        'type': {
            'help': 'Test type: analysis or comparison',
            'choices': ('analysis', 'comparison')
        }
    })

    required_options = ('type',)

    def execute(self):
        self._dispatch('ci-rpmdiff-{}'.format(self.option('type')), self.build_params)
