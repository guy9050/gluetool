import collections
import os
import gluetool
from gluetool.log import log_blob
from gluetool_modules.libs.sut_installation_fail import SUTInstallationFailedError

#: Describes one command used to SUT installtion
#:
#: :ivar str label: Label used for logging.
#: :ivar str command: Command to execute on the guest, executed once for each item from items.
#:                    It can contain a placeholder ({}) which is substituted by the current item.
#: :ivar list(str) items: Items to execute command withreplaced to `command`.
#: :ivar bool ignore_exception: Indicates whether to raise `SUTInstallationFailedError` when command fails.
SUTStep = collections.namedtuple('SUTStep', ['label', 'command', 'items', 'ignore_exception'])


class SUTInstallation(object):

    def __init__(self, directory_name, primary_task):
        self.directory_name = directory_name
        self.primary_task = primary_task
        self.steps = []

    def add_step(self, label, command, items, ignore_exception):
        if not isinstance(items, list):
            items = [items]

        self.steps.append(SUTStep(label, command, items, ignore_exception))

    def run(self, guest):
        log_dir_name = '{}-{}'.format(self.directory_name, guest.name)
        os.mkdir(log_dir_name)

        log_file = None

        for i, step in enumerate(self.steps):
            guest.info(step.label)

            log_file_name = '{}-{}'.format(i, step.label.replace(' ', '-'))
            log_file_path = os.path.join(log_dir_name, log_file_name)

            for item in step.items:
                # `step.command` contains `{}` to indicate place where item is substitute.
                # e.g 'yum install -y {}'.format('ksh')
                command = step.command.format(item)
                try:
                    output = guest.execute(command)
                except gluetool.glue.GlueCommandError:
                    if not step.ignore_exception:
                        raise SUTInstallationFailedError(self.primary_task, guest, item)

                with open(log_file_path, 'a') as log_file:
                    # pylint: disable=unused-argument
                    def write_cover(text, **kwargs):
                        log_file.write('{}\n\n'.format(text))

                    log_blob(write_cover, 'Command', command)
                    log_blob(write_cover, 'Stdout', output.stdout)
                    log_blob(write_cover, 'Stderr', output.stderr)

        guest.info('All packages have been successfully installed')


class InstallCoprBuild(gluetool.Module):
    """
    Installs build packages on given guest.
    """

    name = 'install-copr-build'
    description = 'Install build packages on given guest'

    options = {
        'log-dir-name': {
            'help': 'Name of directory where outputs of installation commands will be stored (default: %(default)s).',
            'type': str,
            'default': 'artifact-installation'
        }
    }

    shared_functions = ('setup_guest',)

    def setup_guest(self, guests, **kwargs):

        self.require_shared('primary_task')

        self.overloaded_shared('setup_guest', guests, **kwargs)

        primary_task = self.shared('primary_task')

        sut_installation = SUTInstallation(self.option('log-dir-name'), primary_task)

        sut_installation.add_step('Download copr repository', 'curl {} --output /etc/yum.repos.d/copr_build.repo',
                                  primary_task.repo_url, False)

        # reinstall command has to be called for each rpm separately, hence list of rpms is used
        sut_installation.add_step('Reinstall packages', 'yum -y reinstall {}', primary_task.rpm_urls, True)

        # downgrade, update and install commands are called just once with all rpms followed, hence list of
        # rpms is joined to one item
        joined_rpm_urls = ' '.join(primary_task.rpm_urls)

        sut_installation.add_step('Downgrade packages', 'yum -y downgrade {}', joined_rpm_urls, True)
        sut_installation.add_step('Update packages', 'yum -y update {}', joined_rpm_urls, True)
        sut_installation.add_step('Install packages', 'yum -y install {}', joined_rpm_urls, True)

        sut_installation.add_step('Verify packages installed', 'rpm -q {}', primary_task.rpm_names, False)

        for guest in guests:
            sut_installation.run(guest)
