import json
import gluetool
from gluetool.log import log_dict
from gluetool.utils import Command
from gluetool import SoftGlueError, GlueError
from libci.sentry import PrimaryTaskFingerprintsMixin


class SUTInstallationFailedError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task, installation_logs):
        super(SUTInstallationFailedError, self).__init__(task, 'SUT installation failed')

        self.installation_logs = installation_logs


class InstallMBSBuild(gluetool.Module):
    """
    Installs packages from specified rhel module on given guest. Calls given ansible playbook
    which downloads repofile and installs module.
    """

    name = 'install-mbs-build'
    description = 'Install module on given guest'

    options = {
        'playbook': {
            'help': 'Ansible playbook, which installs given module',
            'type': str,
            'metavar': 'FILE'
        }
    }

    shared_functions = ('setup_guest',)

    def _get_repo(self, module_name):
        self.info('Generating repo for module via ODCS')
        command = ['odcs', '--redhat', 'create', 'module', module_name,
                   '--sigkey', 'none', '--flag', 'no_deps']
        # TO improve: raise OdcsError if command fails
        output = Command(command).run()
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

    def setup_guest(self, guests, **kwargs):

        self.require_shared('run_playbook', 'primary_task')

        self.overloaded_shared('setup_guest', guests, **kwargs)

        primary_task = self.shared('primary_task')

        module_name = '{}:{}:{}'.format(primary_task.name, primary_task.stream, primary_task.version)
        repo_url = self._get_repo(module_name)
        self.info('Installing module "{}" from {}'.format(module_name, repo_url))

        self.shared('run_playbook', gluetool.utils.normalize_path(self.option('playbook')), guests, variables={
            'REPO_URL': repo_url,
            'MODULE_NAME': module_name,
            'ansible_python_interpreter': '/usr/bin/python3'
        })
        self.info('rhel-module installed')
