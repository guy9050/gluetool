import os
import re
import bs4

import gluetool
from gluetool import utils, SoftGlueError
from libci.sentry import PrimaryTaskFingerprintsMixin


class SUTInstallationFailedError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task, installation_logs):
        super(SUTInstallationFailedError, self).__init__(task, 'SUT installation failed')

        self.installation_logs = installation_logs


class InstallKojiBuild(gluetool.Module):
    """
    Install brew artifact on given guests.
    """

    name = 'install-koji-build'
    description = 'Prepare guests for testing process.'

    shared_functions = ('setup_guest',)

    def _setup_guest(self, guest):
        # pylint: disable=no-self-use
        """
        Run task, which installs brew artifact on SUT
        """

        guest.info('setting the guest up')

        # Install SUT
        self.info('installing the artifact')

        brew_build_task_params = self.shared('brew_build_task_params')

        job_xml = gluetool.utils.render_template("""
            <job>
              <recipeSet priority="Normal">
                <recipe ks_meta="method=http harness='restraint-rhts beakerlib-redhat'" whiteboard="Server">
                  <task name="/distribution/install/brew-build" role="None">
                    <params>
                      <param name="BASEOS_CI" value="true"/>
                      <param name="METHOD" value="{{ BREW_BUILD_PARAMS.METHOD }}"/>
                      <param name="TASKS" value="{{ BREW_BUILD_PARAMS.TASKS }}"/>
                      <param name="BUILDS" value="{{ BREW_BUILD_PARAMS.BUILDS }}"/>
                      <param name="SERVER" value="{{ BREW_BUILD_PARAMS.SERVER }}"/>
                      <param name="RPM_BLACKLIST" value="{{ BREW_BUILD_PARAMS.RPM_BLACKLIST }}"/>
                    </params>
                    <rpm name="test(/distribution/install/brew-build)" path="/mnt/tests/distribution/install/brew-build"/>
                  </task>
                  <task name="/distribution/runtime_tests/verify-nvr-installed" role="None">
                    <params>
                      <param name="BASEOS_CI" value="true"/>
                    </params>
                    <rpm name="test(/distribution/runtime_tests/verify-nvr-installed)" path="/mnt/tests/distribution/runtime_tests/verify-nvr-installed"/>
                  </task>
                </recipe>
              </recipeSet>
            </job>
        """, BREW_BUILD_PARAMS=brew_build_task_params)

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
        self.require_shared('restraint', 'brew_build_task_params')

        self.overloaded_shared('setup_guest', guests, **kwargs)

        for guest in guests:
            self._setup_guest(guest)
