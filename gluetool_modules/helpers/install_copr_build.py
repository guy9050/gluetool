import gluetool
from gluetool_modules.libs.sut_installation import SUTInstallation


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

        sut_installation.add_step('Download copr repository', 'curl -v {} --output /etc/yum.repos.d/copr_build.repo',
                                  items=primary_task.repo_url)

        # reinstall command has to be called for each rpm separately, hence list of rpms is used
        sut_installation.add_step('Reinstall packages', 'yum -y reinstall {}',
                                  items=primary_task.rpm_urls, ignore_exception=True)

        # downgrade, update and install commands are called just once with all rpms followed, hence list of
        # rpms is joined to one item
        joined_rpm_urls = ' '.join(primary_task.rpm_urls)

        sut_installation.add_step('Downgrade packages', 'yum -y downgrade {}', items=joined_rpm_urls, ignore_exception=True)
        sut_installation.add_step('Update packages', 'yum -y update {}', items=joined_rpm_urls, ignore_exception=True)
        sut_installation.add_step('Install packages', 'yum -y install {}', items=joined_rpm_urls, ignore_exception=True)

        sut_installation.add_step('Verify packages installed', 'rpm -q {}', items=primary_task.rpm_names)

        for guest in guests:
            sut_installation.run(guest)
