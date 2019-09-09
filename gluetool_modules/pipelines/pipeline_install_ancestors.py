import six

import gluetool
from gluetool.utils import normalize_shell_option
from gluetool.log import log_dict
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

    def __init__(self, *args, **kwargs):
        super(PipelineInstallAncestors, self).__init__(*args, **kwargs)

        self.context = {}

    def _build_exists(self, name, tag):
        self.require_shared('koji_session')
        koji_session = self.shared('koji_session')
        builds = koji_session.listTagged(tag, package=name, inherit=True, latest=True)

        return len(builds) > 0

    def setup_guest(self, guest, log_dirpath=None, **kwargs):

        log_dirpath = guest_setup_log_dirpath(guest, log_dirpath)

        # make sure previous setup_guest methods are called
        guest_setup_output = self.overloaded_shared('setup_guest', guest, log_dirpath=log_dirpath, **kwargs) or []

        self.info('installing the ancestor {}'.format(self.shared('primary_task').nvr))
        brew_options = normalize_shell_option(self.option('brew-options')) or ''

        # get ancestors of the package in our pipeline and construct options for koji module
        if self.has_shared('ancestors'):

            if not self.option('tag'):
                raise gluetool.glue.GlueError("Option 'tag' is required if used with 'ancestors' shared function.")
            tag = self.option('tag')

            self.require_shared('primary_task')
            component = self.shared('primary_task').component
            ancestors = self.shared('ancestors', component)

            if ancestors:
                log_dict(self.info, "Ancestors of '{}'".format(component), ancestors)
            else:
                self.info("No ancestors of '{}' found, assume ancestor's name is the same.".format(component))
                ancestors = [component]

            self.info("Filter out ancestors without builds tagged '{}'".format(tag))
            ancestors = [ancestor for ancestor in ancestors if self._build_exists(ancestor, tag)]

            if ancestors:
                log_dict(self.info, "Ancestors of '{}' with builds tagged '{}'".format(component, tag), ancestors)

                if brew_options:
                    self.warn('Replacing `brew_options` from ancestors shared function.')

                brew_options = '--tag {} --name {}'.format(tag, ','.join(ancestors))
            else:
                self.info('No ancestors left, nothing will be installed on SUT.')

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

        modules = []

        if brew_options:
            modules += [
                # initiliaze the ancestors builds
                gluetool.glue.PipelineStepModule('brew', argv=normalize_shell_option(brew_options))
            ]
        else:
            self.context = {
                'BUILD_TARGET': self.option('tag'),
            }

        modules += [
            # kick then environment installation preparation according to ancestors build
            gluetool.glue.PipelineStepModule('guest-setup'),
            gluetool.glue.PipelineStepCallback('do_setup_guest', do_setup_guest)
        ]

        if brew_options:
            modules += [
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

    @property
    def eval_context(self):
        __content__ = {  # noqa
            'BUILD_TARGET': """
                            Build target of build we were looking for in case nothing found.
                            If build was found, this value is provided by artifact provider (etc. koji, brew or copr).
                            """
        }

        return self.context
