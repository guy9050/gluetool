import os

import gluetool
from gluetool_modules.libs.artifacts import artifacts_location
from gluetool_modules.libs.guest_setup import guest_setup_log_dirpath, GuestSetupOutput, GuestSetupStage
from gluetool_modules.libs.sut_installation import SUTInstallation

# Type annotations
from typing import Any, List, Optional  # Ignore PyUnusedCodeBear
from libci.guest import Guest


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

    def setup_guest(self, guest, stage=GuestSetupStage.PRE_ARTIFACT_INSTALLATION, log_dirpath=None, **kwargs):
        # type: (Guest, Optional[str], **Any) -> List[GuestSetupOutput]

        self.require_shared('primary_task')

        log_dirpath = guest_setup_log_dirpath(guest, log_dirpath)

        guest_setup_output = self.overloaded_shared(
            'setup_guest',
            guest,
            stage=stage,
            log_dirpath=log_dirpath,
            **kwargs
        ) or []

        if stage != GuestSetupStage.ARTIFACT_INSTALLATION:
            return guest_setup_output

        guest.info('installing the artifact')

        installation_log_dirpath = os.path.join(
            log_dirpath,
            '{}-{}'.format(self.option('log-dir-name'), guest.name)
        )

        primary_task = self.shared('primary_task')

        sut_installation = SUTInstallation(self, installation_log_dirpath, primary_task, logger=guest.logger)

        sut_installation.add_step('Download copr repository', 'curl -v {} --output /etc/yum.repos.d/copr_build.repo',
                                  items=primary_task.repo_url)

        # reinstall command has to be called for each rpm separately, hence list of rpms is used
        sut_installation.add_step('Reinstall packages', 'yum -y reinstall {}',
                                  items=primary_task.rpm_urls, ignore_exception=True)

        # downgrade, update and install commands are called just once with all rpms followed, hence list of
        # rpms is joined to one item
        joined_rpm_urls = ' '.join(primary_task.rpm_urls)

        sut_installation.add_step('Downgrade packages', 'yum -y downgrade {}',
                                  items=joined_rpm_urls, ignore_exception=True)
        sut_installation.add_step('Update packages', 'yum -y update {}',
                                  items=joined_rpm_urls, ignore_exception=True)
        sut_installation.add_step('Install packages', 'yum -y install {}',
                                  items=joined_rpm_urls, ignore_exception=True)

        sut_installation.add_step('Verify packages installed', 'rpm -q {}', items=primary_task.rpm_names)

        # If the installation fails, we won't return GuestSetupOutput instance(s) to the caller,
        # therefore the caller won't have any access to logs, hence nobody would find out where
        # installation logs live. This will be solved one day, when we would be able to propagate
        # output anyway, despite errors. Until that, each guest-setup-like module is responsible
        # for logging location of relevant logs.
        guest.info('Copr build installation logs are in {}'.format(
            artifacts_location(self, installation_log_dirpath, logger=guest.logger)
        ))

        sut_installation.run(guest)

        return guest_setup_output + [
            GuestSetupOutput(
                stage=stage,
                label='Copr build installation',
                log_path=installation_log_dirpath,
                additional_data=sut_installation
            )
        ]
