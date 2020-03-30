import os
import gluetool
from gluetool import GlueError
from gluetool.result import Ok, Error
from gluetool_modules.libs.artifacts import artifacts_location
from gluetool_modules.libs.guest_setup import guest_setup_log_dirpath, GuestSetupOutput, GuestSetupStage
from gluetool_modules.libs.sut_installation import SUTInstallationFailedError


class InstallKojiDockerImage(gluetool.Module):
    """
    Install a Docker image from a Koji/Brew task on a given guest using 'restraint' shared function
    and information provided in the task. Overrides 'guest-setup' shared function, calling the original
    function (e.g. from 'guest-setup' module) before the image installation.
    """

    name = 'install-koji-docker-image'
    description = 'Install a Docker image.'

    shared_functions = ('setup_guest',)

    def setup_guest(self, guest, stage=GuestSetupStage.PRE_ARTIFACT_INSTALLATION, log_dirpath=None, **kwargs):
        self.require_shared('primary_task', 'restraint')

        log_dirpath = guest_setup_log_dirpath(guest, log_dirpath)

        r_overloaded_guest_setup_output = self.overloaded_shared(
            'setup_guest',
            guest,
            stage=stage,
            log_dirpath=log_dirpath,
            **kwargs
        )

        if r_overloaded_guest_setup_output is None:
            r_overloaded_guest_setup_output = Ok([])

        if r_overloaded_guest_setup_output.is_error:
            return r_overloaded_guest_setup_output

        if stage != GuestSetupStage.ARTIFACT_INSTALLATION:
            return r_overloaded_guest_setup_output

        guest_setup_output = r_overloaded_guest_setup_output.unwrap() or []

        installation_log_dirpath = os.path.join(
            log_dirpath,
            'artifact-installation-{}'.format(guest.name)
        )

        task = self.shared('primary_task')

        archive_url = ''
        registry_url = ''

        # If there is a build, there should be an archive in that build, with a link to
        # tar & gzipped image.
        if task.has_build:
            # Find image archives, we'll grab the first one when constructing final XML
            image_archives = [
                archive for archive in task.build_archives
                if archive['btype'] == 'image' and archive['extra']['image']['arch'] == guest.environment.arch
            ]

            if not image_archives:
                raise GlueError('No compatible "image" archive in task {}'.format(task.id))

            archive_url = image_archives[0]['image_url']

        else:
            # Otherwise, take one of the repositories, and use its URL.
            if not task.image_repositories:
                raise GlueError('No image repositories in task {}'.format(task.id))

            registry_url = task.image_repositories[0].url

        source_options = 'IMAGE_ARCHIVE_URL={} IMAGE_REGISTRY_URL={}'.format(archive_url, registry_url)

        # This belongs to some sort of config file... But setting source options
        # is probably a bit too complicated for config file, and it's better to arget it
        # to just a single task instead of using --taskparam & setting them globally.
        job_xmls = self.shared(
            'beaker_job_xml',
            # Force guest's compose as distro - otherwise, `distros` shared would get called,
            # returning pretty much anything since it's out of our control and knows nothing
            # abotu us, dealing with a particular guest.
            distros=[
                guest.environment.compose
            ],
            body_options=[
                '--task=/tools/toolchain-common/Install/configure-extras-repo',
                '--task=/distribution/containers/enable-docker',
                '--task=/distribution/containers/remove-images',
                '--task={} /distribution/containers/install-image'.format(source_options),
                '--task=/distribution/containers/install-test-config'
            ],
            options=[
                # These seem to be important for restraint - probably moving to wow-options-map is the right way,
                # if we could tell we're putting together a recipe for restraint instead of Beaker.
                '--single',
                '--no-reserve',
                '--restraint',
                '--suppress-install-task',
            ] + [
                '--no-arch={}'.format(no_arch)
                for no_arch in task.task_arches.arches if no_arch != guest.environment.arch
            ] + [
                '--arch={}'.format(guest.environment.arch)
            ],
            extra_context={
                'GUEST': guest,
                'PHASE': 'artifact-installation'
            }
        )

        # This is probably not true in general, but our Docker pipelines - in both beaker and openstack - deal
        # with just a single Beaker distro. To avoid any weird errors later, check number of XMLs, but it would
        # be nice to check how hard is this assumption.
        if len(job_xmls) != 1:
            raise gluetool.GlueError('Unexpected number of job XML descriptions')

        job_xml = job_xmls[0]

        gluetool.log.log_xml(self.debug, 'artifact installation job', job_xml)

        output = self.shared('restraint', guest, job_xml,
                             rename_dir_to=installation_log_dirpath)

        index_filepath = os.path.join(installation_log_dirpath, 'index.html')
        index_location = artifacts_location(self, index_filepath, logger=guest.logger)

        guest_setup_output += [
            GuestSetupOutput(
                stage=stage,
                label='Brew/Koji Docker image installation',
                log_path=index_filepath,
                additional_data=output
            )
        ]

        if output.execution_output.exit_code != 0:
            self.debug('restraint exited with invalid exit code {}'.format(output.execution_output.exit_code))

            return Error((
                guest_setup_output,
                SUTInstallationFailedError(
                    self.shared('primary_task'),
                    guest,
                    installation_logs=index_filepath,
                    installation_logs_location=index_location
                )
            ))

        return Ok(guest_setup_output)
