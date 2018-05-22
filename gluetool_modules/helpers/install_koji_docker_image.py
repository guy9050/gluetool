import os
import re
import bs4

import gluetool
from gluetool import utils, GlueError, SoftGlueError
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

        # Find image archives, we'll grab the first one when constructing final XML
        image_archives = [archive for archive in self.shared('primary_task').build_archives
                          if archive['btype'] == 'image']

        if not image_archives:
            raise GlueError('No "image" archive in task {}'.format(self.shared('primary_task').id))

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
                      <param name="IMAGE_URL" value="{{ ARCHIVE['image_url'] }}"/>
                      <param name="IMAGE_NAME" value="{{ PRIMARY_TASK.nvr }}"/>
                    </params>
                    <rpm name="test(/examples/sandbox/emachado/install-docker-image)" path="/mnt/tests/examples/sandbox/emachado/install-docker-image"/>
                  </task>
                  <task name="/examples/sandbox/emachado/install-docker-test-config" role="None">
                    <params>
                      <param name="BASEOS_CI" value="true"/>
                      <param name="IMAGE_COMPONENT" value="{{ PRIMARY_TASK.component }}"/>
                      <param name="IMAGE_BRANCH" value="{{ PRIMARY_TASK.branch }}"/>
                      <param name="IMAGE_TEST_CONFIG" value="/{{ PRIMARY_TASK.nvr }}.yml"/>
                    </params>
                    <rpm name="test(/examples/sandbox/emachado/install-docker-test-config)" path="/mnt/tests/examples/sandbox/emachado/install-docker-test-config"/>
                  </task>
                </recipe>
              </recipeSet>
            </job>
        """, ARCHIVE=image_archives[0], **self.shared('eval_context'))

        job = bs4.BeautifulSoup(job_xml, 'xml')

        output = self.shared('restraint', guest, job)

        sut_install_logs = None

        match = re.search(r'Using (\./tmp[a-zA-Z0-9\._]+?) for job run', output.stdout)
        if match is not None:
            sut_install_logs = '{}/index.html'.format(match.group(1))

            if 'BUILD_URL' in os.environ:
                sut_install_logs = utils.treat_url('{}/artifact/{}'.format(os.getenv('BUILD_URL'), sut_install_logs),
                                                   logger=self.logger)

            self.info('SUT installation logs are in {}'.format(sut_install_logs))

        if output.exit_code != 0:
            self.debug('restraint exited with invalid exit code {}'.format(output.exit_code))

            raise SUTInstallationFailedError(self.shared('primary_task'),
                                             '<Not available>' if sut_install_logs is None else sut_install_logs)

    def setup_guest(self, guests, **kwargs):
        self.require_shared('primary_task', 'restraint')

        self.overloaded_shared('setup_guest', guests, **kwargs)

        for guest in guests:
            self._setup_guest(guest)
