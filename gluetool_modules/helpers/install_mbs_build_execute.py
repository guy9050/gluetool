import json
import gluetool
from gluetool.log import log_dict
from gluetool.utils import Command
from gluetool import GlueError
from gluetool_modules.libs.sut_installation_fail import SUTInstallationFailedError


class InstallMBSBuild(gluetool.Module):
    """
    Installs packages from specified rhel module on given guest. Calls given ansible playbook
    which downloads repofile and installs module.
    """

    name = 'install-mbs-build-execute'
    description = 'Install module on given guest'

    shared_functions = ('setup_guest',)

    options = {
        'installation-workarounds': {
            'help': 'File with commands and rules, when used them.'
        },
        'use-devel-module': {
            'help': 'Use -devel module when generating ODCS repo.',
            'action': 'store_true'
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

        workaround_commands = []

        # callback for 'commands' item in installation_workarounds
        # pylint: disable=unused-argument
        def _add_commands_callback(instruction, command, argument, context):
            log_dict(self.info, 'using following commands', argument)

            workaround_commands.extend(argument)

        self.shared('evaluate_instructions', self.installation_workarounds, {
            'commands': _add_commands_callback,
        })

        nsvc = primary_task.nsvc

        # Include -devel module if requested, for more information see
        #
        #    https://projects.engineering.redhat.com/browse/COMPOSE-2993
        #
        # Note that -devel module can contain some packages people want to use in their tests
        if self.option('use-devel-module'):
            nsvc = '{} {}-devel:{}:{}:{}'.format(
                primary_task.nsvc,
                primary_task.name,
                primary_task.stream,
                primary_task.version,
                primary_task.context
            )

        repo_url = self._get_repo(nsvc, guests)

        for guest in guests:

            try:
                for cmd in workaround_commands:
                    guest.debug('Executing "{} on {}"'.format(cmd, guest))
                    guest.execute(cmd)

                guest.execute('curl -v {} --output /etc/yum.repos.d/mbs_build.repo'.format(repo_url))
                guest.execute('yum module enable -y {}'.format(nsvc))
                guest.execute('yum module install -y {}'.format(nsvc))

            except gluetool.glue.GlueCommandError:
                raise SUTInstallationFailedError(primary_task, guest, nsvc)
