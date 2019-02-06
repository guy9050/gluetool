import gluetool
from gluetool import SoftGlueError
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

        brew_build_task_params = ' '.join([
            '{}="{}"'.format(param, value) for param, value in self.shared('brew_build_task_params').iteritems()
        ])

        # This belongs to some sort of config file... But setting source options
        # is probably a bit too complicated for config file, and it's better to target it
        # to just a single task instead of using --taskparam & setting them globally.
        job_xmls = self.shared('beaker_job_xml', body_options=[
            '--task={} /distribution/install/brew-build'.format(brew_build_task_params),
            '--task=/distribution/runtime_tests/verify-nvr-installed',
            '--task=/distribution/collect_repos'
        ], options=[
            # These seem to be important for restraint - probably moving to wow-options-map is the right way,
            # if we could tell we're putting together a recipe for restraint instead of Beaker.
            '--single',
            '--no-reserve',
            '--restraint',
            '--suppress-install-task',
            '--arch', guest.environment.arch
        ], extra_context={
            'PHASE': 'artifact-installation'
        })

        # This is probably not true in general, but our Docker pipelines - in both beaker and openstack - deal
        # with just a single Beaker distro. To avoid any weird errors later, check number of XMLs, but it would
        # be nice to check how hard is this assumption.
        if len(job_xmls) != 1:
            raise gluetool.GlueError('Unexpected number of job XML descriptions')

        job_xml = job_xmls[0]

        output = self.shared('restraint', guest, job_xml,
                             rename_dir_to='artifact-installation-{}'.format(guest.name),
                             label='Artifact installation logs are in')

        if output.execution_output.exit_code != 0:
            self.debug('restraint exited with invalid exit code {}'.format(output.execution_output.exit_code))

            raise SUTInstallationFailedError(self.shared('primary_task'), output.index_location)

    def setup_guest(self, guests, **kwargs):
        self.require_shared('restraint', 'brew_build_task_params', 'beaker_job_xml')

        self.overloaded_shared('setup_guest', guests, **kwargs)

        for guest in guests:
            self._setup_guest(guest)
