import os

import gluetool

from gluetool.action import Action
from gluetool.result import Ok, Error
from gluetool_modules.libs.artifacts import artifacts_location
from gluetool_modules.libs.guest_setup import guest_setup_log_dirpath, GuestSetupOutput, GuestSetupStage
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

    def setup_guest(self, guest, stage=GuestSetupStage.PRE_ARTIFACT_INSTALLATION, log_dirpath=None, **kwargs):
        self.require_shared('restraint', 'brew_build_task_params', 'beaker_job_xml')

        log_dirpath = guest_setup_log_dirpath(guest, log_dirpath)

        if self.option('skip-overloaded-shared'):
            guest_setup_output = []

        else:
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

            guest_setup_output = r_overloaded_guest_setup_output.unwrap() or []

        if stage != GuestSetupStage.ARTIFACT_INSTALLATION:
            return Ok(guest_setup_output)

        installation_log_dirpath = os.path.join(
            log_dirpath,
            'artifact-installation-{}'.format(guest.name)
        )

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

        index_filepath = os.path.join(installation_log_dirpath, 'index.html')
        index_location = artifacts_location(self, index_filepath, logger=guest.logger)

        guest_setup_output += [
            GuestSetupOutput(
                stage=stage,
                label='Brew/Koji build installation',
                log_path=os.path.join(installation_log_dirpath, 'index.html'),
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
