import os
import gluetool
from gluetool import GlueError
from gluetool_modules.libs.artifacts import artifacts_location
from gluetool_modules.libs.guest_setup import guest_setup_log_dirpath, GuestSetupOutput
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

    def setup_guest(self, guest, log_dirpath=None, **kwargs):
        self.require_shared('primary_task', 'restraint')

        log_dirpath = guest_setup_log_dirpath(guest, log_dirpath)

        installation_log_dirpath = os.path.join(
            log_dirpath,
            'artifact-installation-{}'.format(guest.name)
        )

        guest_setup_output = self.overloaded_shared('setup_guest', guest, log_dirpath=log_dirpath, **kwargs) or []

        guest.info('installing the Docker image')

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

        # If the installation fails, we won't return GuestSetupOutput instance(s) to the caller,
        # therefore the caller won't have any access to logs, hence nobody would find out where
        # installation logs live. This will be solved one day, when we would be able to propagate
        # output anyway, despite errors. Until that, each guest-setup-like module is responsible
        # for logging location of relevant logs.
        index_filepath = os.path.join(installation_log_dirpath, 'index.html')

        guest.info('Brew/Koji Docker image installation logs are in {}'.format(
            artifacts_location(self, index_filepath, logger=guest.logger)
        ))

        if output.execution_output.exit_code != 0:
            self.debug('restraint exited with invalid exit code {}'.format(output.execution_output.exit_code))

            raise SUTInstallationFailedError(
                self.shared('primary_task'),
                guest,
                installation_logs=index_filepath
            )

        guest.info('All packages have been successfully installed')

        return guest_setup_output + [
            GuestSetupOutput(
                label='Brew/Koji Docker image installation',
                log_path=index_filepath,
                additional_data=output
            )
        ]
