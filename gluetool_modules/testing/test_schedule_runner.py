import libci.guest
import gluetool
from gluetool.action import Action
from gluetool.utils import normalize_bool_option
from gluetool_modules.libs.guest_setup import GuestSetupOutput, GuestSetupStage
from gluetool_modules.libs.jobs import JobEngine, Job, handle_job_errors
from gluetool_modules.libs.test_schedule import TestScheduleEntryStage, TestScheduleEntryState

# Type annotations
from typing import TYPE_CHECKING, cast, Any, Callable, Dict, List, Optional  # noqa
from gluetool_modules.libs.test_schedule import TestSchedule, TestScheduleEntry  # noqa


class TestScheduleRunner(gluetool.Module):
    """
    Dispatch tests, carried by schedule entries (`SE`), using runner plugins.

    For each `SE`, a shared function ``run_test_schedule_entry`` is called - this function is provided
    by one or more `runner plugins`, and takes care of executing tests prescribed by the `SE`. This module
    takes care of coordinating the work on the schedule level, updating states of `SEs` as necessary.
    It doesn't care about the tiny details like **how** to run the tests carried by `SE`.

    Runner plugins are expected to provide the shared function which accepts single `SE` and returns nothing.
    Plugin is responsible for updating ``SE.result`` attribute.
    """

    name = 'test-schedule-runner'
    description = 'Dispatch tests, carried by schedule entries (`SE`), using runner plugins.'

    options = {
        'parallelize': {
            'help': 'Enable or disable parallelization of test schedule entries (default: %(default)s)',
            'default': 'no',
            'metavar': 'yes|no'
        }
    }

    @gluetool.utils.cached_property
    def parallelize(self):
        # type: () -> bool

        return normalize_bool_option(self.option('parallelize'))

    def sanity(self):
        # type: () -> None

        if self.parallelize:
            self.info('Will run schedule entries in parallel')

        else:
            self.info('Will run schedule entries serially')

    def _provision_guest(self, schedule_entry):
        # type: (TestScheduleEntry) -> List[libci.guest.NetworkedGuest]

        # This is necessary - the output would tie the thread and the schedule entry in
        # the output. Modules used to actually provision the guest use their own module
        # loggers, therefore there's no connection between these two entities in the output
        # visible to the user with INFO+ log level.
        #
        # I don't like this line very much, it's way too similar to the most common next message:
        # usualy the ``provision`` shared function emits log message of form 'provisioning guest
        # for environment ...', but it's lesser of two evils. The proper solution would be propagation
        # of schedule_entry.logger down the stream for ``provision`` shared function to use. Leaving
        # that as an exercise for long winter evenings...
        schedule_entry.info('starting guest provisioning')

        with Action('provisioning guest', parent=schedule_entry.action, logger=schedule_entry.logger):
            return cast(
                List[libci.guest.NetworkedGuest],
                self.shared('provision', schedule_entry.testing_environment)
            )

    def _setup_guest(self, schedule_entry):
        # type: (TestScheduleEntry) -> Any

        assert schedule_entry.guest is not None

        schedule_entry.info('starting guest setup')

        results = []  # type: List[GuestSetupOutput]

        with Action(
            'pre-artifact-installation guest setup',
            parent=schedule_entry.action,
            logger=schedule_entry.logger
        ):
            results += schedule_entry.guest.setup(stage=GuestSetupStage.PRE_ARTIFACT_INSTALLATION)

        with Action(
            'pre-artifact-installation-workarounds guest setup',
            parent=schedule_entry.action,
            logger=schedule_entry.logger
        ):
            results += schedule_entry.guest.setup(stage=GuestSetupStage.PRE_ARTIFACT_INSTALLATION_WORKAROUNDS)

        with Action(
            'artifact-installation guest setup',
            parent=schedule_entry.action,
            logger=schedule_entry.logger
        ):
            results += schedule_entry.guest.setup(stage=GuestSetupStage.ARTIFACT_INSTALLATION)

        with Action(
            'post-artifact-installation-workarounds guest setup',
            parent=schedule_entry.action,
            logger=schedule_entry.logger
        ):
            results += schedule_entry.guest.setup(stage=GuestSetupStage.POST_ARTIFACT_INSTALLATION_WORKAROUNDS)

        with Action(
            'post-artifact-installation guest setup',
            parent=schedule_entry.action,
            logger=schedule_entry.logger
        ):
            results += schedule_entry.guest.setup(stage=GuestSetupStage.POST_ARTIFACT_INSTALLATION)

        return results

    def _run_tests(self, schedule_entry):
        # type: (TestScheduleEntry) -> None

        schedule_entry.info('starting tests execution')

        with Action('test execution', parent=schedule_entry.action, logger=schedule_entry.logger):
            self.shared('run_test_schedule_entry', schedule_entry)

    def _run_schedule(self, schedule):
        # type: (TestSchedule) -> None

        def _job(schedule_entry, name, target):
            # type: (TestScheduleEntry, str, Callable[[TestScheduleEntry], Any]) -> Job

            return Job(
                logger=schedule_entry.logger,
                name='{}: {}'.format(schedule_entry.id, name),
                target=target,
                args=(schedule_entry,),
                kwargs={}
            )

        def _shift(schedule_entry, new_stage, new_state=None):
            # type: (TestScheduleEntry, TestScheduleEntryStage, Optional[TestScheduleEntryState]) -> None

            old_stage, old_state = schedule_entry.stage, schedule_entry.state

            if new_state is None:
                new_state = old_state

            schedule_entry.stage, schedule_entry.state = new_stage, new_state

            schedule_entry.debug('shifted: {} => {}, {} => {}'.format(
                old_stage.name, new_stage.name, old_state.name, new_state.name
            ))

        def _finish_action(schedule_entry):
            # type: (TestScheduleEntry) -> None

            assert schedule_entry.action is not None

            schedule_entry.action.set_tags({
                'stage': schedule_entry.stage.name,
                'state': schedule_entry.state.name,
                'result': schedule_entry.result.name
            })

            schedule_entry.action.finish()

        def _on_job_start(schedule_entry):
            # type: (TestScheduleEntry) -> None

            if schedule_entry.stage == TestScheduleEntryStage.CREATED:
                schedule_entry.debug('planning guest provisioning')

                _shift(schedule_entry, TestScheduleEntryStage.GUEST_PROVISIONING)

            elif schedule_entry.stage == TestScheduleEntryStage.GUEST_PROVISIONED:
                schedule_entry.debug('planning guest setup')

                _shift(schedule_entry, TestScheduleEntryStage.GUEST_SETUP)

            elif schedule_entry.stage == TestScheduleEntryStage.PREPARED:
                schedule_entry.info('planning test execution')

                _shift(schedule_entry, TestScheduleEntryStage.RUNNING)

        def _on_job_complete(result, schedule_entry):
            # type: (Any, TestScheduleEntry) -> None

            if schedule_entry.stage == TestScheduleEntryStage.GUEST_PROVISIONING:
                schedule_entry.info('guest provisioning finished')

                schedule_entry.guest = result[0]
                _shift(schedule_entry, TestScheduleEntryStage.GUEST_PROVISIONED)

                engine.enqueue_jobs(_job(schedule_entry, 'guest setup', self._setup_guest))

            elif schedule_entry.stage == TestScheduleEntryStage.GUEST_SETUP:
                schedule_entry.info('guest setup finished')

                gluetool.log.log_dict(schedule_entry.debug, 'guest setup outputs', result)

                schedule_entry.guest_setup_outputs = cast(
                    List[GuestSetupOutput],
                    result
                )

                # In a perfect world, we should log each log location here, to provide this functionality
                # to all involved guest-setup-like modules. But their `setup-guest` methods can raise
                # exceptions, that means no `result` for us, and especially in that case we need to know
                # where logs live, therefore at this moment, each module must log the location on its own.
                # When we get access to their output - and errors as well - we re-enable the code below.

                # for output in schedule_entry.guest_setup_outputs:
                #    schedule_entry.info('{} logs are in {}'.format(
                #        output.label,
                #        artifacts_location(self, output.log_path, logger=schedule_entry.logger)
                #    ))

                _shift(schedule_entry, TestScheduleEntryStage.PREPARED)

                engine.enqueue_jobs(_job(schedule_entry, 'running tests', self._run_tests))

            elif schedule_entry.stage == TestScheduleEntryStage.RUNNING:
                schedule_entry.info('test execution finished')

                # Here we should display "test logs are in ..." message like we do for guest-setup,
                # but leaving that for another patch as we don't have unified "report results"
                # structure yet.

                _shift(schedule_entry, TestScheduleEntryStage.COMPLETE)

                _finish_action(schedule_entry)

        def _on_job_error(exc_info, schedule_entry):
            # type: (Any, TestScheduleEntry) -> None

            exc = exc_info[1]

            if schedule_entry.stage == TestScheduleEntryStage.GUEST_PROVISIONING:
                schedule_entry.error('guest provisioning failed: {}'.format(exc), exc_info=exc_info)

            elif schedule_entry.stage == TestScheduleEntryStage.GUEST_SETUP:
                schedule_entry.error('guest setup failed: {}'.format(exc), exc_info=exc_info)

            elif schedule_entry.stage == TestScheduleEntryStage.RUNNING:
                schedule_entry.error('test execution failed: {}'.format(exc), exc_info=exc_info)

            _shift(schedule_entry, TestScheduleEntryStage.COMPLETE, new_state=TestScheduleEntryState.ERROR)

            _finish_action(schedule_entry)

        def _on_job_done(remaining_count, schedule_entry):
            # type: (int, TestScheduleEntry) -> None

            # `remaining_count` is number of remaining jobs, but we're more interested in a number of remaining
            # schedule entries (one entry spawns multiple jobs, hence jobs are not usefull to us).

            remaining_count = len([
                se for se in schedule if se.stage != TestScheduleEntryStage.COMPLETE
            ])

            schedule.log(self.info, label='{} entries pending'.format(remaining_count))

        self.shared('trigger_event', 'test-schedule.start',
                    schedule=schedule)

        schedule.log(self.info, label='running test schedule of {} entries'.format(len(schedule)))

        engine = JobEngine(
            logger=self.logger,
            on_job_start=_on_job_start,
            on_job_complete=_on_job_complete,
            on_job_error=_on_job_error,
            on_job_done=_on_job_done
        )

        for schedule_entry in schedule:
            # We spawn new action for each schedule entry - we don't enter its context anywhere though!
            # It serves only as a link between "schedule" action and "doing X to move entry forward" subactions,
            # capturing lifetime of the schedule entry. It is then closed when we switch the entry to COMPLETE
            # stage.

            assert schedule_entry.testing_environment is not None

            schedule_entry.action = Action(
                'processing schedule entry',
                parent=schedule.action,
                logger=schedule_entry.logger,
                tags={
                    'entry-id': schedule_entry.id,
                    'runner-capability': schedule_entry.runner_capability,
                    'testing-environment': schedule_entry.testing_environment.serialize_to_json()
                }
            )

            engine.enqueue_jobs(_job(schedule_entry, 'provisioning', self._provision_guest))

        engine.run()

        if engine.errors:
            self.shared('trigger_event', 'test-schedule.error',
                        schedule=schedule, errors=engine.errors)

            handle_job_errors(engine.errors, 'At least one entry crashed')

        self.shared('trigger_event', 'test-schedule.finished',
                    schedule=schedule)

    def execute(self):
        # type: () -> None

        schedule = cast(
            TestSchedule,
            self.shared('test_schedule') or []
        )

        with Action('executing test schedule', parent=Action.current_action(), logger=self.logger) as schedule.action:
            self._run_schedule(schedule)

            schedule.action.set_tag('result', schedule.result.name)
