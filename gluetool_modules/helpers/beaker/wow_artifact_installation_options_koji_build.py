import gluetool
from gluetool.log import log_dict


class WowArtifactInstallationOptionsKojiBuild(gluetool.Module):
    """
    Provides options for ``workflow-tomorrow`` which, when passed to ``wow``, enhance the final job
    with steps necessary to install the Koji (or Brew) task under the test.
    """

    name = 'wow-artifact-installation-options-koji-build'
    description = 'Create options for ``workflow-options`` to install the Koji/Brew task.'
    shared_functions = ('wow_artifact_installation_options',)

    # pylint: disable=invalid-name
    def wow_artifact_installation_options(self):
        """
        Return list of ``workflow-tomorrow`` options to install the Koji/Brew tasks.

        :rtype: list(str)
        """

        self.require_shared('brew_build_task_params')

        options = [
            '--first-testing-task=/distribution/runtime_tests/verify-nvr-installed'
        ]

        # create options for brew-build task
        brew_build_task_params = self.shared('brew_build_task_params')

        # and convert them to a space-separated list of params, with values wrapped
        # by the quotes: <param>="foo bar" <param>="baz" ...
        brew_build_task_params = ' '.join([
            '{}="{}"'.format(param, value) for param, value in brew_build_task_params.iteritems()
        ])

        # this should be '--init-task="{} /....', but it's not a problem for python-based programs (bkr)
        # and sclrun can handle it too
        options += [
            '--init-task={} /distribution/install/brew-build'.format(brew_build_task_params)
        ]

        log_dict(self.debug, 'wow options for Brew/Koji artifact installation', options)

        return options
