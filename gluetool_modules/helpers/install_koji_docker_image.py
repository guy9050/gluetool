import bs4

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

        # Need to finish: we have to chose the correct architecture. Right now, there is just
        # x86_64, and we're not propagating this information correctly - probably guest should
        # know what architecture it provides, and the code bellow would use it to pick correct
        # image.

        # If there is a build, there should be an archive in that build, with a link to
        # tar & gzipped image.
        if task.has_build:
            # Find image archives, we'll grab the first one when constructing final XML
            image_archives = [
                archive for archive in task.build_archives if archive['btype'] == 'image'
            ]

            if not image_archives:
                raise GlueError('No "image" archive in task {}'.format(task.id))

            archive_url = image_archives[0]['image_url']

        else:
            # Otherwise, take one of the repositories, and use its URL.
            if not task.image_repositories:
                raise GlueError('No image repositories in task {}'.format(task.id))

            registry_url = task.image_repositories[0].url

        # One day, when we start using wow to construct this job, the params below would be injected
        # by wow-options map. Until then, we have to specify them here *and* in the wow-options map...
        job_xml = gluetool.utils.render_template("""
            <job>
              <recipeSet priority="Normal">
                <recipe ks_meta="method=http harness='restraint-rhts beakerlib-redhat'" whiteboard="Server">
                  <task name="/tools/toolchain-common/Install/configure-extras-repo" role="None">
                    <params>
                      <param name="BASEOS_CI" value="true"/>
                    </params>
                    <rpm name="test(/tools/toolchain-common/Install/configure-extras-repo)" path="/mnt/tests/tools/toolchain-common/Install/configure-extras-repo"/>
                  </task>
                  <task name="/examples/sandbox/emachado/enable-docker" role="None">
                    <params>
                      <param name="BASEOS_CI" value="true"/>
                    </params>
                    <rpm name="test(/examples/sandbox/emachado/enable-docker)" path="/mnt/tests/examples/sandbox/emachado/enable-docker"/>
                  </task>
                  <task name="/examples/sandbox/emachado/install-docker-image" role="None">
                    <params>
                      <param name="BASEOS_CI" value="true"/>
                      <param name="IMAGE_ARCHIVE_URL" value="{{ ARCHIVE_URL }}"/>
                      <param name="IMAGE_REGISTRY_URL" value="{{ REGISTRY_URL }}"/>
                      <param name="IMAGE_NAME" value="{{ PRIMARY_TASK.nvr | lower }}"/>
                    </params>
                    <rpm name="test(/examples/sandbox/emachado/install-docker-image)" path="/mnt/tests/examples/sandbox/emachado/install-docker-image"/>
                  </task>
                  <task name="/examples/sandbox/emachado/install-docker-test-config" role="None">
                    <params>
                      <param name="BASEOS_CI" value="true"/>
                      <param name="IMAGE_COMPONENT" value="{{ PRIMARY_TASK.component }}"/>
                      <param name="IMAGE_BRANCH" value="{{ PRIMARY_TASK.branch }}"/>
                      <param name="IMAGE_TEST_CONFIG" value="/{{ PRIMARY_TASK.nvr }}.yml"/>
                      <param name="IMAGE_ARCH" value="{{ PRIMARY_TASK.task_arches.arches[0] }}" />
                      <param name="IMAGE_VERSION" value="{{ PRIMARY_TASK.version }}"/>
                      <param name="IMAGE_RELEASE" value="{{ PRIMARY_TASK.release }}"/>
                    </params>
                    <rpm name="test(/examples/sandbox/emachado/install-docker-test-config)" path="/mnt/tests/examples/sandbox/emachado/install-docker-test-config"/>
                  </task>
                </recipe>
              </recipeSet>
            </job>
        """, ARCHIVE_URL=archive_url, REGISTRY_URL=registry_url, **self.shared('eval_context'))

        job = bs4.BeautifulSoup(job_xml, 'xml')

        output = self.shared('restraint', guest, job,
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
