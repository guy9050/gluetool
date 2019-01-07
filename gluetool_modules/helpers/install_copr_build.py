import gluetool
from gluetool.log import log_dict
from gluetool import SoftGlueError
from libci.sentry import PrimaryTaskFingerprintsMixin
# pylint: disable=no-name-in-module
from jq import jq


class SUTInstallationFailedError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task, guest, packages):
        super(SUTInstallationFailedError, self).__init__(task, 'SUT installation failed')

        self.guest = guest
        self.packages = packages


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
        rpm_names = sum([task.rpm_names for task in tasks], [])

        log_dict(self.debug, 'RPMs to install', rpm_names)
        log_dict(self.debug, 'RPMs install from', rpm_urls)

        interpreters = self.shared('detect_ansible_interpreter', guests[0])

        _, ansible_output = self.shared(
            'run_playbook',
            gluetool.utils.normalize_path(self.option('playbook')),
            guests,
            variables={
                'PACKAGE_URLS': rpm_urls,
                'PACKAGE_NAMES': rpm_names,
                'ansible_python_interpreter': interpreters[0]
            },
            json_output=True
        )

        query = """
              .plays[].tasks[]
            | select(.task.name == "NVR check")
            | .hosts | to_entries[]
            | {
                host: .key,
                packages: [
                    .value.results[]
                  | select(.failed == true)
                  | .item
                ]
              }
            | select(.packages != [])""".replace('\n', '')

        failed_tasks = jq(query).transform(ansible_output, multiple_output=True)

        log_dict(self.debug, 'ansible output after jq processing', failed_tasks)

        if failed_tasks:
            first_fail = failed_tasks[0]
            guest = [guest for guest in guests if guest.hostname == first_fail['host']][0]
            packages = first_fail['packages']

            guest.warn('Packages {} have not been installed.'.format(','.join(packages)))
            raise SUTInstallationFailedError(self.shared('primary_task'), guest, packages)

        self.info('All packages have been successfully installed')
