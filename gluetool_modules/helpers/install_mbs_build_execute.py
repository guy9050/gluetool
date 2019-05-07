import json
import re
import gluetool
from gluetool.log import log_dict
from gluetool.utils import Command
from gluetool import GlueError
from gluetool_modules.libs.sut_installation import SUTInstallation


class InstallMBSBuild(gluetool.Module):
    """
    Installs packages from specified rhel module on given guest. Calls given ansible playbook
    which downloads repofile and installs module.
    """

    name = 'install-mbs-build-execute'
    description = 'Install module on given guest'

    shared_functions = ('setup_guest',)

    options = {
        'profile': {
            'help': 'Use given profile for module installation',
        },
        'installation-workarounds': {
            'help': 'File with commands and rules, when used them.'
        },
        'use-devel-module': {
            'help': 'Use -devel module when generating ODCS repo.',
            'action': 'store_true'
        },
        'log-dir-name': {
            'help': 'Name of directory where outputs of installation commands will be stored (default: %(default)s).',
            'type': str,
            'default': 'artifact-installation'
        }
    }

    def _get_repo(self, module_nsvc, guests):
        self.info('Generating repo for module via ODCS')

        command = [
            'odcs',
            '--redhat', 'create',
            'module', module_nsvc,
            '--sigkey', 'none'
        ]

        # Inner list gather all arches, `set` gets rid of duplicities, and final `list` converts set to a list.
        for arch in list(set([guest.environment.arch for guest in guests])):
            command += [
                '--arch', arch
            ]

        try:
            output = Command(command).run()
        except gluetool.glue.GlueCommandError:
            raise GlueError('ODCS call failed')

        # strip 1st line before json data
        output = output.stdout[output.stdout.index('{'):]
        output_json = json.loads(output)
        log_dict(self.debug, 'odcs output', output_json)
        state = output_json['state_name']
        if state != 'done':
            raise GlueError('Getting repo from ODCS failed')
        repo_url = output_json['result_repofile']
        self.info('Module repo from ODCS: {}'.format(repo_url))
        return repo_url

    @gluetool.utils.cached_property
    def installation_workarounds(self):
        if not self.option('installation-workarounds'):
            return []

        return gluetool.utils.load_yaml(self.option('installation-workarounds'), logger=self.logger)

    def setup_guest(self, guests, **kwargs):

        self.require_shared('primary_task', 'evaluate_instructions')

        self.overloaded_shared('setup_guest', guests, **kwargs)

        primary_task = self.shared('primary_task')

        nsvc = nsvc_odcs = primary_task.nsvc

        #
        # Include -devel module if requested, for more information see
        #   https://projects.engineering.redhat.com/browse/COMPOSE-2993
        #
        # Note that -devel module can contain some packages people want to use in their tests
        #
        if self.option('use-devel-module'):
            # we will use devel module for installation
            nsvc = '{}-devel:{}:{}:{}'.format(
                primary_task.name,
                primary_task.stream,
                primary_task.version,
                primary_task.context
            )

            # For ODCS request we need to include both modules, for installation we will use only -devel if requested
            nsvc_odcs = '{} {}'.format(primary_task.nsvc, nsvc)

        repo_url = self._get_repo(nsvc_odcs, guests)

        #
        # Some modules do not provide 'default' module and user needs to explicitly specify it, for more info see
        #   https://projects.engineering.redhat.com/browse/OSCI-56
        #
        if self.option('profile'):
            nsvc = '{}/{}'.format(nsvc, self.option('profile'))

        sut_installation = SUTInstallation(self.option('log-dir-name'), primary_task)

        # callback for 'commands' item in installation_workarounds
        # pylint: disable=unused-argument
        def _add_step_callback(instruction, command, argument, context):
            for step in argument:
                sut_installation.add_step(step['label'], step['command'])

        self.shared('evaluate_instructions', self.installation_workarounds, {
            'steps': _add_step_callback,
        })

        sut_installation.add_step(
            'Download ODCS repo', 'curl -v {} --output /etc/yum.repos.d/mbs_build.repo',
            items=repo_url
        )
        sut_installation.add_step('Reset module', 'yum module reset -y {}', items=nsvc)
        sut_installation.add_step('Enable module', 'yum module enable -y {}', items=nsvc)
        sut_installation.add_step('Install module', 'yum module install -y {}', items=nsvc)

        def _check_installed(command, output):
            # type: (str, gluetool.utils.ProcessOutput) -> None
            """
            Process output of `yum module info` command and raises `gluetool.glue.GlueCommandError` if it is incorrect.
            """

            odcs_part = None

            for part in output.stdout.split('\n\n'):
                if re.search(r'Repo\s*:\s*odcs-\d+', part):
                    odcs_part = part

            if not odcs_part:
                self.error("Module '{}' is not provided by ODCS repo".format(nsvc))
                raise gluetool.glue.GlueCommandError(command, output)

            if self.option('profile'):
                profile = self.option('profile')
            else:
                match = re.search(r'Default profiles\s*:\s*(.*)', odcs_part)
                profile = match.group(1) if match else 'UNKNOWN-DEFAULT-PROFILE'

            if not re.search(r'Profiles\s*:.*{}(?: \[d\])? \[i\]'.format(profile), odcs_part):
                self.error("Profile '{}' is not installed".format(profile))
                raise gluetool.glue.GlueCommandError(command, output)

            if not re.search(r'Stream\s*:\s*{} (?:\[d\])?\[e\] ?\[a\]'.format(primary_task.stream), odcs_part):
                self.error("Stream '{}' is not active or enabled".format(primary_task.stream))
                raise gluetool.glue.GlueCommandError(command, output)

        sut_installation.add_step('Verify module installed', 'yum module info {}',
                                  items=nsvc, callback=_check_installed)

        for guest in guests:
            sut_installation.run(guest)
