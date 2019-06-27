import gluetool
import libci.dispatch_job


class RpminspectJob(libci.dispatch_job.DispatchJenkinsJobMixin, gluetool.Module):
    """
    Jenkins job module dispatching RPMinspect analysis and comparison testing, as defined in
    ``ci-test-brew-rpminspect_analysis.yaml`` and ``ci-test-brew-rpminspect_comparison.yaml`` files.

    .. note::

       Value of the ``--id`` option is, by default, first searched in the environment, at it is expected
       to be set by Jenkins' machinery, e.g. by the ``redhat-ci-plugin``.

    .. note::

       This module dispatches a Jenkins job, therefore it requires other module to provide connection
       to a Jenkins instance via the shared function ``jenkins``.
    """

    name = 'rpminspect-job'
    description = 'Job module dispatching RPMinspect analysis and comparison pipeline.'

    # DispatchJenkinsJobMixin.options contain hard defaults
    # pylint: disable=gluetool-option-hard-default
    options = gluetool.utils.dict_update({}, libci.dispatch_job.DispatchJenkinsJobMixin.options, {
        'type': {
            'help': 'Test type: analysis or comparison',
            'choices': ('analysis', 'comparison')
        }
    })

    required_options = ('type',)

    def execute(self):
        self.shared('jenkins').invoke_job('ci-test-brew-rpminspect_{}'.format(self.option('type')), self.build_params)
