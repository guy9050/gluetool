import six

import gluetool
from gluetool.utils import normalize_shell_option
from gluetool.log import log_dict
from gluetool_modules.libs.guest_setup import guest_setup_log_dirpath, GuestSetupStage


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
        },
        'install-rpms-blacklist': {
            'help': """
                Value is passed to inner called `brew-build-task-params` module (default: %(default)s).
                """,
            'type': str,
            'default': ''
        },
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

    @gluetool.utils.cached_property
    def _brew_options(self):
        brew_options = normalize_shell_option(self.option('brew-options')) or ''

        if not self.has_shared('ancestors'):
            return brew_options

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

        return brew_options

    def setup_guest(self, guest, stage=GuestSetupStage.PRE_ARTIFACT_INSTALLATION, log_dirpath=None, **kwargs):
        log_dirpath = guest_setup_log_dirpath(guest, log_dirpath)

        # Make sure previous setup_guest methods are called. This is out of decency only - we don't expect there
        # to be any other `setup_guest` in the pipeline. If there were, it would be operate within the context
        # of the initial primary artifact while we're trying to do our job within context of the ancestor.
        guest_setup_output = self.overloaded_shared(
            'setup_guest',
            guest,
            stage=stage,
            log_dirpath=log_dirpath,
            **kwargs
        ) or []

        # callback to initiate setup guest in separate pipeline
        def do_setup_guest(self):
            guest_setup_output.extend(
                self.shared(
                    'setup_guest',
                    guest,
                    stage=stage,
                    log_dirpath=log_dirpath,
                    **kwargs
                )
            )

        #
        # Run the installation of the ancestors in a separate pipeline. We are using a separate pipeline
        # so we do not spoil the parent pipeline with the build initialization.
        #
        # Please note that we are already in 'setup_guest' function here, and will be requiring to kick
        # additional ``setup_guest`` for modules in the separate pipeline. For that kick we use a helper
        # function ``do_guest_setup``.
        #

        modules = []

        # If we have an ancestor build, by adding `brew` module at the beginning of our pipeline we're running
        # all the modules in the context of the ancestor build.
        if self._brew_options:
            modules += [
                gluetool.glue.PipelineStepModule('brew', argv=normalize_shell_option(self._brew_options))
            ]

        else:
            # When there's no artifact we'd inject into our child pipeline, we try at least to "fake" its presence
            # by providing dummy eval context content, to fool modules that need it, like guest-setup and its
            # method of picking playbooks via map based on artifact's build target.
            self.context = {
                'BUILD_TARGET': self.option('tag'),
            }

        # We always want to run guest-setup (or any other module hooked on setup_guest function), for all
        # stages.
        modules += [
            gluetool.glue.PipelineStepModule('guest-setup'),
            gluetool.glue.PipelineStepCallback('do_setup_guest', do_setup_guest)
        ]

        # In the artifact-installation stage, throw in modules to install the ancestor.
        if stage == GuestSetupStage.ARTIFACT_INSTALLATION and self._brew_options:
            self.info('installing the ancestor {}'.format(self.shared('primary_task').nvr))

            blacklist = self.option('install-rpms-blacklist')
            brew_build_task_params_argv = ['--install-rpms-blacklist', blacklist] if blacklist else []

            modules += [
                gluetool.glue.PipelineStepModule('brew-build-task-params', argv=brew_build_task_params_argv),
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
