import gluetool
import libci.dispatch_job


class TeDuDeJob(libci.dispatch_job.DispatchJenkinsJobMixin, gluetool.Module):
    """
    Jenkins job module dispatching TeDuDe validation testing, as defined in
    ``ci-test-brew-tedude.yaml`` file

    .. note::

       Value of the ``--id`` option is, by default, first searched in the environment, at it is expected
       to be set by Jenkins' machinery, e.g. by the ``redhat-ci-plugin``.

    .. note::

       This module dispatches a Jenkins job, therefore it requires other module to provide connection
       to a Jenkins instance via the shared function ``jenkins``.
    """

    name = 'tedude-job'
    description = 'Job module dispatching TeDuDe validation test.'

    # DispatchJenkinsJobMixin.options contain hard defaults
    # pylint: disable=gluetool-option-no-default-in-help,gluetool-option-hard-default
    options = gluetool.utils.dict_update({}, libci.dispatch_job.DispatchJenkinsJobMixin.options, {
        'bug-attributes': {
            'help': 'Comma separated list of bug attribute names. (default: %(default)s)',
            'type': str,
            'default': None
        },
        'tedude-instructions': {
            'help': 'Name of the instructions file for ``tedude`` module',
            'type': str,
            'default': None
        }
    })

    def execute(self):
        if self.option('tedude-instructions'):
            self.build_params['tedude_options'] = '--instructions={}'.format(self.option('tedude-instructions'))

        if self.option('bug-attributes'):
            self.build_params['tedude_options'] = '--bugzilla-attributes={}'.format(self.option('bug-attributes'))

        self.shared('jenkins').invoke_job('ci-test-brew-tedude', self.build_params)
