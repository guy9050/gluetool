import six

import gluetool
from gluetool.utils import normalize_shell_option
from gluetool_modules.libs.guest_setup import guest_setup_log_dirpath


class PipelineInstallAncestors(gluetool.Module):
    """
    Installs package ancestors in a separate pipeline.

    The ancestor package to install can specified by ``brew-options``, which is passed directly
    to the ``brew`` module. If ``ancestors`` shared function exists, the ancestors package(s) are resolved
    from ``primary_task`` component name. Then these component names are used to resolve specific brew
    builds on the given tag specifed by the option ``tag``.

    Guest is setup by `guest-setup` module.
    """
    name = 'pipeline-install-ancestors'

    options = {
        'brew-options': {
            'help': 'Options to pass to brew module.',
        },
        'tag': {
            'help': 'Tag to use when looking up ancestors.'
        }
    }

    shared_functions = ('setup_guest',)

    def setup_guest(self, guest, log_dirpath=None, **kwargs):

        log_dirpath = guest_setup_log_dirpath(guest, log_dirpath)

        # make sure previous setup_guest methods are called
        guest_setup_output = self.overloaded_shared('setup_guest', guest, log_dirpath=log_dirpath, **kwargs) or []

        self.info('installing the ancestor {}'.format(self.shared('primary_task').nvr))
        brew_options = normalize_shell_option(self.option('brew-options'))

        # get ancestors of the package in our pipeline and construct options for koji module
        if self.has_shared('ancestors'):

            if not self.option('tag'):
                raise gluetool.glue.GlueError("Option 'tag' is required if used with 'ancestors' shared function.")

            if brew_options:
                self.warn('replacing brew_options from ancestors shared function')

            self.require_shared('primary_task')
            ancestors = self.shared('ancestors', self.shared('primary_task').component)

            brew_options = '--tag {} --name {}'.format(self.option('tag'), ','.join(ancestors))

        # callback to initiate setup guest in separate pipeline
        def do_setup_guest(self):
            guest_setup_output.extend(self.shared('setup_guest', guest, log_dirpath=log_dirpath, **kwargs))

        #
        # Run the installation of the ancestors in a separate pipeline. We are using a separate pipeline
        # so we do not spoil the parent pipeline with the build initialization.
        #
        # Please note that we are already in 'setup_guest' function here, and will be requiring to kick
        # additional ``setup_guest`` for modules in the separate pipeline. For that kick we use a helper
        # function ``do_guest_setup``.
        #

        modules = [
            # initiliaze the ancestors builds
            gluetool.glue.PipelineStepModule('brew', argv=normalize_shell_option(brew_options)),

            # kick then environment installation preparation according to ancestors build
            gluetool.glue.PipelineStepModule('guest-setup'),
            gluetool.glue.PipelineStepCallback('do_setup_guest', do_setup_guest),

            # kick the installation of the ancestors
            gluetool.glue.PipelineStepModule('brew-build-task-params'),
            gluetool.glue.PipelineStepModule('install-koji-build', argv=['--skip-overloaded-shared']),
            gluetool.glue.PipelineStepCallback('do_setup_guest', do_setup_guest)
        ]

        failure_execute, failure_destroy = self.glue.run_modules(modules)

        if failure_execute:
            six.reraise(*failure_execute.exc_info)

        if failure_destroy:
            six.reraise(*failure_destroy.exc_info)

        return guest_setup_output
