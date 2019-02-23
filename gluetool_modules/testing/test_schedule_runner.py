import sys

from six import reraise

import gluetool
from gluetool.utils import normalize_bool_option
from gluetool_modules.libs.jobs import Job, run_jobs, handle_job_errors
from gluetool_modules.libs.test_schedule import TestScheduleEntryStage, TestScheduleEntryState

# Type annotations
# pylint: disable=unused-import,wrong-import-order,ungrouped-imports
from typing import TYPE_CHECKING, cast, Any, Dict, List  # noqa
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

    def execute(self):
        # type: () -> None

        schedule = cast(
            TestSchedule,
            self.shared('test_schedule') or []
        )

        self.shared('trigger_event', 'test-schedule-runner.start',
                    schedule=schedule)

        if self.parallelize:
            self.info('Scheduled {} entries, running them in parallel'.format(len(schedule)))

        else:
            self.info('Scheduled {} entries, running them one by one'.format(len(schedule)))

        def _run_test_schedule_entry_wrapper(schedule_entry):
            # type: (TestScheduleEntry) -> None

            schedule_entry.info('starting test runner thread')

            try:
                self.shared('run_test_schedule_entry', schedule_entry)

            # pylint: disable=broad-except
            except Exception:
                exc_info = sys.exc_info()

                schedule_entry.exception('test runner exited with an error')

                self.glue.sentry_submit_exception(gluetool.Failure(self, exc_info), logger=schedule_entry.logger)

                # And re-raise it when we're done with it - the error handling code wants
                # to see all exceptions, and wants to raise the best one.
                reraise(*exc_info)

        # Prepare list of jobs and callbacks for ``run_jobs``.
        jobs = [
            Job(logger=se.logger, target=_run_test_schedule_entry_wrapper, args=(se,), kwargs={}) for se in schedule
        ]

        def _before_job_start(schedule_entry):
            # type: (TestScheduleEntry) -> None

            schedule_entry.info('starting to run tests')
            schedule_entry.stage = TestScheduleEntryStage.RUNNING
            schedule_entry.log()

            self.shared('trigger_event', 'test-schedule-runner.schedule-entry.started',
                        schedule_entry=schedule_entry)

        def _on_job_complete(result, schedule_entry):
            # type: (Any, TestScheduleEntry) -> None

            # pylint: disable=unused-argument

            schedule_entry.info('test runner finished')
            schedule_entry.stage = TestScheduleEntryStage.COMPLETE

            self.shared('trigger_event', 'test-schedule-runner.schedule-entry.complete',
                        schedule_entry=schedule_entry)

        def _on_job_error(exc_info, schedule_entry):
            # type: (Any, TestScheduleEntry) -> None

            # pylint: disable=unused-argument

            schedule_entry.error('test runner failed')

            schedule_entry.stage = TestScheduleEntryStage.COMPLETE
            schedule_entry.state = TestScheduleEntryState.ERROR

            self.shared('trigger_event', 'test-schedule-runner.schedule-entry.error',
                        schedule_entry=schedule_entry)

        def _on_job_done(remaining_count, schedule_entry):
            # type: (int, TestScheduleEntry) -> None

            # pylint: disable=unused-argument

            schedule.log(self.info, label='{} entries pending'.format(remaining_count))

            self.shared('trigger_event', 'test-schedule-runner.schedule-entry.finished',
                        schedule_entry=schedule_entry)

        schedule.log(self.info, label='{} entries pending'.format(len(schedule)))

        job_errors = run_jobs(
            jobs,
            logger=self.logger,
            max_workers=(len(schedule) if self.parallelize else 1),
            worker_name_prefix='test-runner-thread',
            on_job_start=_before_job_start,
            on_job_complete=_on_job_complete,
            on_job_error=_on_job_error,
            on_job_done=_on_job_done
        )

        if job_errors:
            handle_job_errors(job_errors, 'At least one test runner failed')

        self.shared('trigger_event', 'test-schedule-runner.finished',
                    schedule=schedule)
