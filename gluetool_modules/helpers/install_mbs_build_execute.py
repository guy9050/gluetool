import json
import os
import re
import gluetool
from gluetool.log import log_dict
from gluetool.utils import Command
from gluetool import GlueError

from gluetool_modules.libs.artifacts import artifacts_location
from gluetool_modules.libs.guest_setup import guest_setup_log_dirpath, GuestSetupOutput
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
        'enable-only': {
            'help': 'Module is only enabled, not installed.',
            'action': 'store_true'
        },
        'log-dir-name': {
            'help': 'Name of directory where outputs of installation commands will be stored (default: %(default)s).',
            'type': str,
            'default': 'artifact-installation'
        }
    }

    def _get_repo(self, module_nsvc, guest):
        self.info('Generating repo for module via ODCS')

        command = [
            'odcs',
            '--redhat', 'create',
            'module', module_nsvc,
            '--sigkey', 'none'
        ]

        # Inner list gather all arches, `set` gets rid of duplicities, and final `list` converts set to a list.
        command += [
            '--arch', guest.environment.arch
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

    def setup_guest(self, guest, log_dirpath=None, **kwargs):
        self.require_shared('primary_task', 'evaluate_instructions')

        log_dirpath = guest_setup_log_dirpath(guest, log_dirpath)

        installation_log_dirpath = os.path.join(
            log_dirpath,
            '{}-{}'.format(self.option('log-dir-name'), guest.name)
        )

        log_dict(guest.debug, 'setup log directories', [
            log_dirpath, installation_log_dirpath
        ])

        guest_setup_output = self.overloaded_shared('setup_guest', guest, log_dirpath=log_dirpath, **kwargs) or []

        guest.info('installing the artifact')

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

        repo_url = self._get_repo(nsvc_odcs, guest)

        #
        # Some modules do not provide 'default' module and user needs to explicitly specify it, for more info see
        #   https://projects.engineering.redhat.com/browse/OSCI-56
        #

        # using dictionary with one item to be able modify this value from inner functions, since python 2 does not
        # support `nonlocal`
        profile = {}

        if self.option('profile'):
            profile['profile'] = self.option('profile')
            nsvc = '{}/{}'.format(nsvc, profile['profile'])

        sut_installation = SUTInstallation(self, installation_log_dirpath, primary_task, logger=guest)

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

        def _find_odcs_part(output):
            for part in output.split('\n\n'):
                if re.search(r'Repo\s*:\s*odcs-\d+', part):
                    return part

            return None

        def _verify_profile(command, output):
            odcs_part = _find_odcs_part(output.stdout)

            if not odcs_part:
                return "Module '{}' is not provided by ODCS repo".format(nsvc)

            profiles = None
            match = re.search(r'Profiles\s*:\s*(.+)', odcs_part)
            if match:
                profiles = match.group(1).split(',')
                profiles = [re.sub(r'\s*(?:\[d\])?(?: \[i])?', '', item) for item in profiles]

            if not profiles:
                return "Module '{}' does not have any profiles".format(nsvc)

            log_dict(self.debug, 'Available profiles', profiles)

            if not profile:
                match = re.search(r'Default profiles\s*:\s*(.*)', odcs_part)
                if match:
                    profile['profile'] = match.group(1)
                    self.info("Using default profile '{}'".format(profile['profile']))
                else:
                    return "Module '{}' doesn't have default profile set".format(nsvc)

            if profile['profile'] not in profiles:
                return "Profile '{}' is not available".format(profile['profile'])

            return None

        def _check_enabled(command, output):
            # type: (str, gluetool.utils.ProcessOutput) -> None
            """
            Process output of `yum module info` command and returns description of issue, when output is not correct.
            """

            odcs_part = _find_odcs_part(output.stdout)

            if not odcs_part:
                return "Module '{}' is not provided by ODCS repo".format(nsvc)

            if not re.search(r'Stream\s*:\s*{} (?:\[d\])?\[e\] ?\[a\]'.format(primary_task.stream), odcs_part):
                return "Stream '{}' is not active or enabled".format(primary_task.stream)

            return None

        def _check_installed(command, output):
            # type: (str, gluetool.utils.ProcessOutput) -> None
            """
            Process output of `yum module info` command and returns description of issue, when output is not correct.
            """

            odcs_part = _find_odcs_part(output.stdout)

            if not odcs_part:
                return "Module '{}' is not provided by ODCS repo".format(nsvc)

            if not re.search(r'Profiles\s*:.*{}(?: \[d\])? \[i\]'.format(profile['profile']), odcs_part):
                return "Profile '{}' is not installed".format(profile['profile'])

            return None

        if not self.option('enable-only'):
            sut_installation.add_step('Verify profile', 'yum module info {}',
                                      items=nsvc, callback=_verify_profile)

        sut_installation.add_step('Reset module', 'yum module reset -y {}', items=nsvc)
        sut_installation.add_step('Enable module', 'yum module enable -y {}', items=nsvc)
        sut_installation.add_step('Verify module enabled', 'yum module info {}',
                                  items=nsvc, callback=_check_enabled)

        if not self.option('enable-only'):
            sut_installation.add_step('Install module', 'yum module install -y {}', items=nsvc)
            sut_installation.add_step('Verify module installed', 'yum module info {}',
                                      items=nsvc, callback=_check_installed)

        # If the installation fails, we won't return GuestSetupOutput instance(s) to the caller,
        # therefore the caller won't have any access to logs, hence nobody would find out where
        # installation logs live. This will be solved one day, when we would be able to propagate
        # output anyway, despite errors. Until that, each guest-setup-like module is responsible
        # for logging location of relevant logs.
        guest.info('module installation logs are in {}'.format(
            artifacts_location(self, installation_log_dirpath, logger=guest.logger)
        ))

        sut_installation.run(guest)

        return guest_setup_output + [
            GuestSetupOutput(
                label='module installation',
                log_path=installation_log_dirpath,
                additional_data=sut_installation
            )
        ]
