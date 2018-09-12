import gluetool
from gluetool import GlueError, SoftGlueError
from libci.sentry import PrimaryTaskFingerprintsMixin


class SUTInstallationFailedError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task, installation_logs):
        super(SUTInstallationFailedError, self).__init__(task, 'SUT installation failed')

        self.installation_logs = installation_logs


class InstallKojiDockerImage(gluetool.Module):
    """
    Install a Docker image from a Koji/Brew task on a given guest using 'restraint' shared function
    and information provided in the task. Overrides 'guest-setup' shared function, calling the original
    function (e.g. from 'guest-setup' module) before the image installation.
    """

    name = 'install-koji-docker-image'
    description = 'Install a Docker image.'

    shared_functions = ('setup_guest',)

    def _setup_guest(self, guest):
        # pylint: disable=no-self-use
        """
        Run Beaker task to "install" the image.
        """

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
                if archive['btype'] == 'image' and archive['extra']['image']['arch'] == guest.arch
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
        job_xmls = self.shared('beaker_job_xml', body_options=[
            '--task=/tools/toolchain-common/Install/configure-extras-repo',
            '--task=/examples/sandbox/emachado/enable-docker',
            '--task=/examples/sandbox/emachado/remove-docker-images',
            '--task={} /examples/sandbox/emachado/install-docker-image'.format(source_options),
            '--task=/examples/sandbox/emachado/install-docker-test-config'
        ], options=[
            # These seem to be important for restraint - probably moving to wow-options-map is the right way,
            # if we could tell we're putting together a recipe for restraint instead of Beaker.
            '--single',
            '--no-reserve',
            '--restraint',
            '--suppress-install-task',
        ] + [
            '--no-arch={}'.format(no_arch) for no_arch in task.task_arches.arches if no_arch != guest.arch
        ] + [
            '--arch={}'.format(guest.arch)
        ], extra_context={
            'GUEST': guest,
            'PHASE': 'artifact-installation'
        })

        # This is probably not true in general, but our Docker pipelines - in both beaker and openstack - deal
        # with just a single Beaker distro. To avoid any weird errors later, check number of XMLs, but it would
        # be nice to check how hard is this assumption.
        if len(job_xmls) != 1:
            raise gluetool.GlueError('Unexpected number of job XML descriptions')

        job_xml = job_xmls[0]

        gluetool.log.log_xml(self.debug, 'artifact installation job', job_xml)

        output = self.shared('restraint', guest, job_xml,
                             rename_dir_to='artifact-installation-{}'.format(guest.name),
                             label='Artifact installation logs are in')

        if output.execution_output.exit_code != 0:
            self.debug('restraint exited with invalid exit code {}'.format(output.execution_output.exit_code))

            raise SUTInstallationFailedError(self.shared('primary_task'), output.index_location)

    def setup_guest(self, guests, **kwargs):
        self.require_shared('primary_task', 'restraint')

        self.overloaded_shared('setup_guest', guests, **kwargs)

        for guest in guests:
            self._setup_guest(guest)
