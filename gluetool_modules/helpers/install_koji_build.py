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

    options = {
        'install-task-not-build': {
            'help': 'Try to install SUT using brew task ID as a referrence, instead of the brew build ID.',
            'action': 'store_true',
            'default': False
        },
        'install-rpms-blacklist': {
            # pylint: disable=line-too-long
            'help': 'Regexp pattern (compatible with ``egrep``) - when installing build, matching packages will not be installed.',
            'type': str,
            'default': ''
        },
        'install-method': {
            'help': 'Yum method to use for installation (default: ``install``).',
            'type': str,
            'default': 'install'
        }
    }

    shared_functions = ('setup_guest',)

    def _setup_guest(self, tasks, guest):
        # pylint: disable=no-self-use
        """
        Run task, which installs brew artifact on SUT
        """

        guest.info('setting the guest up')

        # Install SUT
        self.info('installing the SUT packages')

        options = {
            'brew_method': self.option('install-method'),
            'brew_tasks': [],
            'brew_builds': [],
            'brew_server': self.shared('primary_task').ARTIFACT_NAMESPACE,
            'rpm_blacklist': self.option('install-rpms-blacklist')
        }

        if self.option('install-task-not-build'):
            self.debug('asked to install by task ID')

            options['brew_tasks'] = [task.task_id for task in tasks]

        else:
            for task in tasks:
                if task.scratch:
                    self.debug('task {} is a scratch build, using task ID for installation'.format(task.id))

                    options['brew_tasks'].append(task.task_id)

                else:
                    self.debug('task {} is a regular task, using build ID for installation'.format(task.id))

                    options['brew_builds'].append(task.build_id)

        options['brew_tasks'] = ' '.join(str(i) for i in options['brew_tasks'])
        options['brew_builds'] = ' '.join(str(i) for i in options['brew_builds'])

        job_xml = """
            <job>
              <recipeSet priority="Normal">
                <recipe ks_meta="method=http harness='restraint-rhts beakerlib-redhat'" whiteboard="Server">
                  <task name="/distribution/install/brew-build" role="None">
                    <params>
                      <param name="BASEOS_CI" value="true"/>
                      <param name="METHOD" value="{brew_method}"/>
                      <param name="TASKS" value="{brew_tasks}"/>
                      <param name="BUILDS" value="{brew_builds}"/>
                      <param name="SERVER" value="{brew_server}"/>
                      <param name="RPM_BLACKLIST" value="{rpm_blacklist}"/>
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
        """.format(**options)

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

        self.require_shared('tasks', 'restraint')

        self.overloaded_shared('setup_guest', guests, **kwargs)

        tasks = self.shared('tasks')
        for guest in guests:
            self._setup_guest(tasks, guest)
