import six

import gluetool
from gluetool.utils import normalize_shell_option, normalize_multistring_option


class PipelineInstallAncestors(gluetool.Module):
    """
    Installs package ancestors in a separate pipeline.

    The ancestor package to install can specified by ``brew-options``, which is passed directly
    to the ``brew`` module. If ``ancestors`` shared function exists, the ancestors package(s) are resolved
    from ``primary_task`` component name. Then these component names are used to resolve specific brew
    builds on the given tag specifed by the option ``tag``.

    Guest is setup by `guest-setup` module and by Ansible playbook(s) (specifed by the option ``playbook``).
    List of primary task rpm urls is passed to the playbook as variable PACKAGE_URLS.
    """
    name = 'pipeline-install-ancestors'

    options = {
        'brew-options': {
            'help': 'Options to pass to brew module.',
        },
        'tag': {
            'help': 'Tag to use when looking up ancestors.'
        },
        'playbooks': {
            'help': 'Path to Ansible playbook(s) to run BEFORE installing the ancestors (default: none).',
            'default': [],
            'action': 'append'
        }
    }

    shared_functions = ('setup_guest',)
    required_options = ('playbooks',)

    def setup_guest(self, guests, *args, **kwargs):

        # make sure previous setup_guest methods are called
        self.overloaded_shared('setup_guest', guests, **kwargs)

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
            self.shared('setup_guest', guests, *args, **kwargs)

        playbooks = normalize_multistring_option(self.option('playbooks'))
        rpm_urls = self.shared('primary_task').rpm_urls
        rpm_urls = ['"{}"'.format(rpm_url) for rpm_url in rpm_urls]

        def run_playbook(self):
            # run all playbooks specified
            for playbook in playbooks:
                self.shared(
                    'run_playbook',
                    playbook,
                    guests,
                    variables={'PACKAGE_URLS': '[{}]'.format(','.join(rpm_urls))},
                )

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

            # run the playbooks before installation
            gluetool.glue.PipelineStepCallback('run_playbook', run_playbook),

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
