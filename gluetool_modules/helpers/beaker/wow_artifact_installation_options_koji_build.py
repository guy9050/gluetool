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

    def wow_artifact_installation_options(self):
        """
        Return list of ``workflow-tomorrow`` options to install the Koji/Brew tasks.

        :rtype: list(str)
        """

        self.require_shared('brew_build_task_params')

        # create options for brew-build task
        brew_build_task_params = self.shared('brew_build_task_params')

        # and convert them to a space-separated list of params, with values wrapped
        # by the quotes: <param>="foo bar" <param>="baz" ...
        brew_build_task_params = ' '.join([
            '{}="{}"'.format(param, value) for param, value in brew_build_task_params.iteritems()
        ])

        options = [
            # This should be '--init-task="{} /....', but it's not a problem for python-based programs (bkr)
            # and sclrun can handle it too
            '--init-task={} /distribution/install/brew-build'.format(brew_build_task_params),

            # We should keep the order of "install" - "verify" steps - wow may rewrite these options
            # to use something better supported by the actual tool used (e.g. wow-tomorrow vs. wow-autofs),
            # e.g. by converting these `-foo-task` options into pure `--task` options, we should help
            # by using them in a correct order since we *do* know their order is important (even when
            # we don't have to do it for wow).
            '--first-testing-task=/distribution/runtime_tests/verify-nvr-installed'
        ]

        log_dict(self.debug, 'wow options for Brew/Koji artifact installation', options)

        return options
