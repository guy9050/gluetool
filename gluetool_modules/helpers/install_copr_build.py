import gluetool
from gluetool.log import log_dict


class InstallCoprBuild(gluetool.Module):
    """
    Installs build packages on given guest. Calls given ansible playbook
    and provides list of package names and list of urls to it.
    """

    name = 'install-copr-build'
    description = 'Install build packages on given guest'

    options = {
        'playbook': {
            'help': 'Ansible playbook, which installs given packages',
            'type': str,
            'metavar': 'FILE'
        }
    }

    shared_functions = ('setup_guest',)

    def setup_guest(self, guests, **kwargs):

        self.require_shared('run_playbook', 'primary_task')

        self.overloaded_shared('setup_guest', guests, **kwargs)

        tasks = self.shared('tasks')
        rpm_urls = sum([task.rpm_urls for task in tasks], [])

        log_dict(self.debug, 'RPMs to install', rpm_urls)

        self.shared('run_playbook', gluetool.utils.normalize_path(self.option('playbook')), guests, variables={
            'PACKAGE_URLS': rpm_urls
        })
