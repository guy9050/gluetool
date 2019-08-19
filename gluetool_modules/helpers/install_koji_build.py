import os

import gluetool

from gluetool.action import Action
from gluetool_modules.libs.artifacts import artifacts_location
from gluetool_modules.libs.guest_setup import guest_setup_log_dirpath, GuestSetupOutput
from gluetool_modules.libs.sut_installation import SUTInstallationFailedError


class InstallKojiBuild(gluetool.Module):
    """
    Install brew artifact on given guests.
    """

    name = 'install-koji-build'
    description = 'Prepare guests for testing process.'

    options = {
        'skip-overloaded-shared': {
            'help': 'Skip calling of overloaded shared',
            'action': 'store_true'
        }
    }

    shared_functions = ('setup_guest',)

    def setup_guest(self, guest, log_dirpath=None, **kwargs):
        self.require_shared('restraint', 'brew_build_task_params', 'beaker_job_xml')

        log_dirpath = guest_setup_log_dirpath(guest, log_dirpath)

        installation_log_dirpath = os.path.join(
            log_dirpath,
            'artifact-installation-{}'.format(guest.name)
        )

        guest_setup_output = []

        if not self.option('skip-overloaded-shared'):
            guest_setup_output = self.overloaded_shared('setup_guest', guest, log_dirpath=log_dirpath, **kwargs) or []

        self.info('installing the artifact')

        brew_build_task_params = self.shared('brew_build_task_params')

        brew_build_task_params = ' '.join([
            '{}="{}"'.format(param, value) for param, value in self.shared('brew_build_task_params').iteritems()
        ])

        # This belongs to some sort of config file... But setting source options
        # is probably a bit too complicated for config file, and it's better to target it
        # to just a single task instead of using --taskparam & setting them globally.
        with Action(
            'preparing brew/koji build installation recipe',
            parent=Action.current_action(),
            logger=guest.logger,
            tags={
                'guest': {
                    'hostname': guest.hostname,
                    'environment': guest.environment.serialize_to_json()
                },
                'artifact-id': self.shared('primary_task').id,
                'artifact-type': self.shared('primary_task').ARTIFACT_NAMESPACE
            }
        ):
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

        with Action(
            'running brew/koji build installation recipe',
            parent=Action.current_action(),
            logger=guest.logger,
            tags={
                'guest': {
                    'hostname': guest.hostname,
                    'environment': guest.environment.serialize_to_json()
                },
                'artifact-id': self.shared('primary_task').id,
                'artifact-type': self.shared('primary_task').ARTIFACT_NAMESPACE
            }
        ):
            output = self.shared('restraint', guest, job_xml,
                                 rename_dir_to=installation_log_dirpath)

        # If the installation fails, we won't return GuestSetupOutput instance(s) to the caller,
        # therefore the caller won't have any access to logs, hence nobody would find out where
        # installation logs live. This will be solved one day, when we would be able to propagate
        # output anyway, despite errors. Until that, each guest-setup-like module is responsible
        # for logging location of relevant logs.
        index_filepath = os.path.join(installation_log_dirpath, 'index.html')
        index_location = artifacts_location(self, index_filepath, logger=guest.logger)

        guest.info('Brew/Koji build installation logs are in {}'.format(index_location))

        if output.execution_output.exit_code != 0:
            self.debug('restraint exited with invalid exit code {}'.format(output.execution_output.exit_code))

            raise SUTInstallationFailedError(
                self.shared('primary_task'),
                guest,
                installation_logs=index_filepath,
                installation_logs_location=index_location
            )

        guest.info('All packages have been successfully installed')

        return guest_setup_output + [
            GuestSetupOutput(
                label='Brew/Koji build installation',
                log_path=index_filepath,
                additional_data=output
            )
        ]
