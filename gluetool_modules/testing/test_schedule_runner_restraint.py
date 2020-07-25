"""
Runs tasks, carried by schedule entries (`SE`), using ``restraint``. `SEs` can be running in parallel,
tasks in each `SE` can be running in isolation, it's a mess. Let's split things into manageable chunks.

Module focuses on working with `task sets` (`TS`). `TS` is nothing more than a list of tasks with their options.

 * each `TS` is wrapped by necessary XML elements to form a job description
 * the job is then given to ``restraint`` to perform it
 * when ``restraint`` reports back to us, we take all data left by ``restraint``
 * based on these data, a ``TaskSetResults`` instance (`TSR`) is created
 * for each task ran, one ``TestRun`` (`TR`) instance is added to `TSR`. It bundles together task name,
   `SE` the task comes from, and actual "results of the task run" - opaque blob of data whose
   internal structure is not important.

Each `SE` contains a pile of tasks, there often are multiple `SEs` in a schedule. To split them into `TS`s,
depending on module options, several methods are applied:

 * with snapshots available and enabled, each task in `SE` represents a single `TS` - module handles as many `TS`s
   as there are tasks in the `SE`, and produces as many `TSR`s
 * without snapshots, all tasks in `SE` are treated as a single `TS`.

Given the split, `TS`s often share guests (when they originated from the same `SE`), these are ran sequentialy.

All `TSR`s produced are then merged into a single one, carrying all results module got for all `SE`s and their tasks.
"""

import collections
import copy
import os
import sys

import enum
import bs4

from six import reraise

import gluetool
from gluetool import GlueError
from gluetool.log import log_blob, log_dict, format_xml
from gluetool.utils import new_xml_element, normalize_bool_option, render_template
import gluetool_modules.libs.guest

from gluetool_modules.libs.artifacts import artifacts_location
from gluetool_modules.libs.test_schedule import TestScheduleResult
from gluetool_modules.testing.test_scheduler_beaker_xml import TestScheduleEntry

# Type annotations
from typing import cast, Any, Dict, List, NamedTuple, Optional  # noqa


# The exit status values come from restraint sources: https://github.com/p3ck/restraint/blob/master/src/errors.h
# I failed to find any documentation on this...
class RestraintExitCodes(enum.IntEnum):
    RESTRAINT_ERROR = 1
    RESTRAINT_TASK_RUNNER_WATCHDOG_ERROR = 4
    RESTRAINT_TASK_RUNNER_RESULT_ERROR = 10
    RESTRAINT_SSH_ERROR = 14


#: Represents a single run of a task and results of this run.
#:
#: :ivar str name: name of the task
#: :ivar libs.test_schedule.TestScheduleEntry schedule_entry: test schedule entry the task belongs to.
#: :ivar dict results: results of the task run, as returned by ``parse_beah_result`` shared function.
TaskRun = NamedTuple('TaskRun', (
    ('name', str),
    ('schedule_entry', TestScheduleEntry),
    ('results', Dict[str, Any])
))

#: Represents results of a set of tasks.
#:
#: Does not carry any info on originating schedule entry because - as we merge more and more
#: task set results into the last one standing, inevitably we end up with task runs originating
#: in different schedule entries in the same task set results instance.
#:
#: :ivar dict tasks: mapping between name of the task and a list of its runs.
TaskSetResults = NamedTuple('TaskSetResults', (
    ('tasks', Dict[str, List[TaskRun]]),
))


def _to_builtins(task_set_results):
    # type: (TaskSetResults) -> Dict[str, List[Any]]
    """
    Serialize task set results - a named tuple - into builtin types like dictionaries, tuples and lists.
    There is some loss of information - schedule entry info is lost, therefore its testing environment
    is gone as well - but nothing down the road is using or storing this kind of data, so until anything
    learns to do that, we can live happily with losing data.
    """

    return collections.OrderedDict([
        (task_name, [task_run.results for task_run in task_runs])
        for task_name, task_runs in task_set_results.tasks.iteritems()
    ])


def _log_task_set_results(schedule_entry, label, task_set_results):
    # type: (TestScheduleEntry, str, TaskSetResults) -> None

    log_dict(schedule_entry.debug, label, _to_builtins(task_set_results))


def _merge_task_set_results(*task_sets):
    # type: (*TaskSetResults) -> TaskSetResults
    """
    Multiple task set results may contain tasks of the same name. This methods merges results of the task, spread
    across multiple task set results, into a single list.

    .. code-block:: yaml

        task set #1:
            task A:
            - result #1
            task B:
            - result #2

        task set #2:
            task A: result #3
            task B: result #4

    becomes

    .. code-block:: yaml

        task set #1:
            task A:
            - result #1
            - result #3
            task B:
            - result #2
            - result #4

    :param TaskSetResults task_sets: Multiple task set results to be merged.
    :rtype: TaskSetResults
    """

    merged = TaskSetResults(tasks=collections.OrderedDict())

    for task_set in task_sets:
        for task_runs in task_set.tasks.itervalues():
            for task_run in task_runs:
                # defaultdict(list) would work but when it's dumped into log, it looks nasty
                if task_run.name not in merged.tasks:
                    merged.tasks[task_run.name] = []

                merged.tasks[task_run.name].append(task_run)

    return merged


class RestraintRunner(gluetool.Module):
    """
    Runs tests from a test schedule, prepared by a scheduler module, using ``restraint`` harness.
    It is able to make use of snapshots (when supported by guests) to isolate each test,
    and is able to parallelize tests.

    The results are provided in the form similar to what ``beaker`` module does - short summary
    in console log, artifact file, and shared function to publish results for later
    modules as well.

    Plugin for the "test schedule" workflow.
    """

    name = 'test-schedule-runner-restraint'
    description = 'Runs tests from a test schedule, prepared by a scheduler module, using ``restraint`` harness.'

    options = {
        'on-error-snapshot': {
            'help': """
                    If set, on crash of restraint take a snapshot of the guest and download it (default: %(default)s).
                    """,
            'default': 'no',
            'metavar': 'yes|no'
        },
        'on-error-continue': {
            'help': """
                    If set, on crash of restraint continue with another test case (default: %(default)s).

                    Be aware that you probably wish to use snapshots as well, as crashed restraint might
                    have left the SUT in a very bad state.
                    """,
            'default': 'no',
            'metavar': 'yes|no'
        },
        'results-directory-template': {
            'help': """
                    If set, directories created by ``restraint`` are renamed using this template. Common eval
                    context is available, with addition of ``GUEST`` (default: %(default)s).

                    Not compatible with ``--use-snapshots``.
                    """,
            'default': None
        },
        'ignore-avc': {
            'help': 'Don\'t report task with failed AVC checks as failed. (default: %(default)s',
            'default': 'no',
            'metavar': 'yes|no'
        }
    }

    shared_functions = ['run_test_schedule_entry', 'serialize_test_schedule_entry_results']

    @gluetool.utils.cached_property
    def on_error_snapshot(self):
        # type: () -> bool

        return normalize_bool_option(self.option('on-error-snapshot'))

    @gluetool.utils.cached_property
    def on_error_continue(self):
        # type: () -> bool

        return normalize_bool_option(self.option('on-error-continue'))

    @gluetool.utils.cached_property
    def ignore_avc(self):
        # type: () -> bool

        return normalize_bool_option(self.option('ignore-avc'))

    def _gather_task_set_results(self, schedule_entry, output):
        # type: (TestScheduleEntry, Any) -> TaskSetResults
        """
        ``restraint`` produces `job.xml` which carries pile of logs, results and so on. We gather necessary
        resources, like ``job.xml``, journal and similar, and hand them to a Beah result parser. It will
        return unified form of results in a form of dictionary. We don't really care what's inside the
        dictionary, we're just prepare all resources and pass the dictionary further.

        :param libs.test_schedule.TestScheduleEntry schedule_entry: test schedule entry the results belongs to.
        :rtype: TaskSetResults
        """

        assert schedule_entry.testing_environment is not None
        assert schedule_entry.guest is not None
        assert schedule_entry.guest.environment is not None

        # XML produced by restraint
        with open(os.path.join(output.directory, 'job.xml'), 'r') as f:
            schedule_entry.debug('XML produced by restraint lies in {}'.format(f.name))

            job_results = bs4.BeautifulSoup(f.read(), 'xml')

        results = TaskSetResults(tasks=collections.OrderedDict())

        def artifact_path(current_location):
            # type: (str) -> str

            return artifacts_location(
                self,
                os.path.join(output.directory, current_location),
                logger=schedule_entry.logger
            )

        for task_results in job_results.recipeSet.recipe.find_all('task'):
            task_name = task_results['name']

            # defaultdict(list) would work but when it's dumped into log, it looks nasty
            if task_name not in results.tasks:
                results.tasks[task_name] = []

            # find journal if there's one available for the task
            journal = None

            journal_log = task_results.logs.find_all('log', filename='journal.xml')
            if journal_log:
                with open(os.path.join(output.directory, journal_log[0]['path']), 'r') as f:
                    schedule_entry.debug('Journal lies in {}'.format(f.name))

                    journal = bs4.BeautifulSoup(f.read(), 'xml').BEAKER_TEST

            # If guest provides hostname, export it to result as well - user may need to connect
            # to it, and its hostname (as set during its setup) may not be resolvable - but
            # we're guaranteed to connect to the guest using guest.hostname value.
            if hasattr(schedule_entry.guest, 'hostname'):
                connectable_hostname = schedule_entry.guest.hostname

            # This is a dictionary with info on results...
            crunched_task_results = self.shared('parse_beah_result', task_results,
                                                journal=journal, recipe=job_results.job.recipeSet,
                                                artifact_path=artifact_path, connectable_hostname=connectable_hostname)

            # ... but we want to track few other bits, therefore re-packaging it as a `TaskRun` instance
            task_results = TaskRun(name=task_name, schedule_entry=schedule_entry, results=crunched_task_results)

            # Since it's not possible to propagate the fields of TaskRun outside of this module - TaskRun
            # and TaskSetResult instances are converted to builtin types before being published as a "result"
            # - and since we are interested in seeing the testing environment the task ran in, let's
            # export the environment into the result directly. Hopefully, in the future, we'd be more
            # explicit on structures and their fields, and then we can easily remove this export, relying
            # on this soon-to-apearch mechanism to propagate all relevant bits into results' payload.
            crunched_task_results['testing-environments'] = {
                'requested': schedule_entry.testing_environment.serialize_to_json(),
                'provisioned': schedule_entry.guest.environment.serialize_to_json()
            }

            results.tasks[task_name].append(task_results)

        return results

    def _run_task_set(self,
                      schedule_entry,  # type: TestScheduleEntry
                      task_set,  # type: List[Any]
                      recipe_attrs,  # type: Dict[str, str]
                      recipe_set_attrs,  # type: Dict[str, str]
                      actual_guest=None  # type: Optional[gluetool_modules.libs.guest.NetworkedGuest]
                     ):  # noqa
        # type: (...) -> TaskSetResults
        """
        Run a set of tasks on the guest.

        :param libs.test_schedule.TestScheduleEntry schedule_entry: Test schedule entry task set belongs to.
        :param task_set: list of <task/> elements, representing separate tasks.
        :param dict recipe_attrs: additional attributes to set on <recipe/> element.
        :param dict recipe_set_attrs: additional attributes to set on <recipe_set/> element.
        :param libc.guest.Guest actual_guest: if set, it is used to host tests instead of ``schedule_entry.guest``.
        :rtype: TaskSetResults
        """

        assert schedule_entry.guest is not None

        self.shared('trigger_event', 'test-schedule-runner-restraint.task-set.started',
                    schedule_entry=schedule_entry, task_set=task_set)

        guest = actual_guest or schedule_entry.guest

        # Log our task set
        schedule_entry.debug('running task set:\n{}'.format('\n'.join([format_xml(task) for task in task_set])))

        # Wrap task set in <job><recipeSet><recipe>... envelope
        job = new_xml_element('job')
        new_xml_element('recipeSet', _parent=job, **recipe_set_attrs)
        new_xml_element('recipe', _parent=job.recipeSet, **recipe_attrs)

        for task in task_set:
            job.recipeSet.recipe.append(copy.copy(task))

        # We'll need this for restraint
        job_desc = format_xml(job)

        log_blob(schedule_entry.debug, 'task set job', job_desc)

        def download_snapshot():
            # type: () -> None

            # If snapshot downloads are not enabled, just do nothing and return.
            if not self.on_error_snapshot:
                return

            try:
                filename = guest.create_snapshot(start_again=False).download()

                schedule_entry.warn("Snapshot saved as '{}'".format(filename))

            except Exception as exc:
                schedule_entry.error('Exception raised when downloading a snapshot: {}'.format(exc))

        # Run restraint with our job. So far, any exception is a serious concern as it signals something
        # bad happened - `restraint` shared function returns restraint's output even if its exit status
        # was non-zero. Take a snapshot, if asked to do so, and re-raise the exception.
        try:
            # We should add rename_dir_to and label but it's not clear what should be the name. Probably something
            # with index of the job. Future patch :)

            # For now, use simple template, which is disabled when parallelization is enabled.
            rename_dir_to = None

            if self.option('results-directory-template'):
                context = gluetool.utils.dict_update(
                    self.shared('eval_context'),
                    {
                        'GUEST': guest
                    }
                )

                rename_dir_to = render_template(self.option('results-directory-template'),
                                                logger=schedule_entry.logger,
                                                **context)

            output = self.shared('restraint', guest, job,
                                 rename_dir_to=rename_dir_to)

            index_filepath = os.path.join(output.directory, 'index.html')

            schedule_entry.info('restraint logs are in {}'.format(
                artifacts_location(self, index_filepath, logger=schedule_entry.logger)
            ))

        except gluetool.GlueError:
            exc_info = sys.exc_info()

            download_snapshot()

            self.shared('trigger_event', 'test-schedule-runner-restraint.task-set.crashed',
                        schedule_entry=schedule_entry, task_set=task_set)

            reraise(*exc_info)

        log_blob(schedule_entry.info, 'Task set output', output.execution_output.stdout)

        # Find out what are the results - `restraint` returned back to us, and even with a non-zero
        # exit status, there should be some results to pick up.
        results = self._gather_task_set_results(schedule_entry, output)

        _log_task_set_results(schedule_entry, 'task set results', results)

        exit_code = output.execution_output.exit_code

        # A zero exit status? Fine!
        if exit_code == 0:
            self.shared('trigger_event', 'test-schedule-runner-restraint.task-set.finished',
                        schedule_entry=schedule_entry, task_set=task_set,
                        output=output, results=results)

            return results

        schedule_entry.debug('restraint exited with invalid exit code {}'.format(exit_code))

        if exit_code == RestraintExitCodes.RESTRAINT_TASK_RUNNER_RESULT_ERROR \
           or (
               exit_code == RestraintExitCodes.RESTRAINT_ERROR
               and output.execution_output.stderr
               and '[restraint-error-quark, 10]' in output.execution_output.stderr
           ):

            if self.ignore_avc:
                # In case no tasks that aren't AVC checks failed we don't want to report it as an error

                def failed(phases):
                    # type: (List[Dict[str, Any]]) -> bool

                    # returns True if task run result has phases with 'FAIL' result and those phases are not AVC checks,
                    # since we don't want to report failed AVC checks as a task's overall fail.
                    # Otherwise returns False.
                    return any([phase for phase in phases if phase['result'] == 'FAIL' and
                                not phase['name'].endswith('avc_check')])

                for task in results.tasks:
                    for task_run in results.tasks[task]:
                        if failed(task_run.results['bkr_phases']):
                            schedule_entry.error('One or more tasks failed')
                            self.shared('trigger_event', 'test-schedule-runner-restraint.task-set.finished',
                                        schedule_entry=schedule_entry, task_set=task_set,
                                        output=output, results=results)
                            return results

                schedule_entry.warning('AVC error(s) found')

            else:
                # "One or more tasks failed" error - this is a good, well behaving error.
                # We can safely move on and return results we have.
                schedule_entry.error('One or more tasks failed')

            self.shared('trigger_event', 'test-schedule-runner-restraint.task-set.finished',
                        schedule_entry=schedule_entry, task_set=task_set,
                        output=output, results=results)

            return results

        if exit_code == RestraintExitCodes.RESTRAINT_TASK_RUNNER_WATCHDOG_ERROR:
            self.info('restraint reports: Watchdog timer exceeded')

            self.shared('trigger_event', 'test-schedule-runner-restraint.task-set.finished',
                        schedule_entry=schedule_entry, task_set=task_set,
                        output=output, results=results)

            return results

        # Now we're dealing with an error we don't know how to handle better, so...

        self.shared('trigger_event', 'test-schedule-runner-restraint.task-set.crashed',
                    schedule_entry=schedule_entry, task_set=task_set,
                    output=output, results=results)

        # Download a snapshot.
        download_snapshot()

        # Return a result and let the caller to decide what to do next.
        if self.on_error_continue:
            return results

        # Restraint failed, and no better option was enabled => raise an exception.
        raise gluetool.GlueError('restraint command exited with return code {}: {}'.format(
            exit_code, output.execution_output.stderr))

    def _run_schedule_entry_isolated_snapshots(self, schedule_entry):
        # type: (TestScheduleEntry) -> TaskSetResults
        """
        Run tasks from a schedule entry one by one, isolated from each other by restoring a base snapshot
        of the guest before running new task.

        :param libs.test_schedule.TestScheduleEntry schedule_entry: Test schedule entry.
        :rtype: TaskSetResults
        """

        assert schedule_entry.guest is not None

        guest, recipe_set = schedule_entry.guest, schedule_entry.recipe_set
        tasks = recipe_set.find_all('task')

        schedule_entry.info('running {} tasks one by one'.format(len(tasks)))

        # _run_task_set will need these, to make tasks feel like home
        recipe_set_attrs = recipe_set.attrs
        recipe_attrs = recipe_set.find_all('recipe')[0].attrs

        # save current state of guest
        base_snapshot = guest.create_snapshot()

        # We're running each task as a unit on its own, as a separate task set, therefore we're gonna
        # get multiple task set results for a single schedule entry. Later we'll merge them into
        # a single `TaskSetResults` instance.
        results = []

        for i, task in enumerate(tasks, 1):
            schedule_entry.info('running task #{} of {}'.format(i, len(tasks)))

            guest.debug("restoring snapshot '{}' before running next task".format(base_snapshot.name))
            actual_guest = cast(
                gluetool_modules.libs.guest.NetworkedGuest,
                guest.restore_snapshot(base_snapshot)
            )

            results.append(self._run_task_set(schedule_entry, [task], recipe_attrs, recipe_set_attrs,
                                              actual_guest=actual_guest))

        return _merge_task_set_results(*results)

    def _run_schedule_entry_isolated_single(self, schedule_entry):
        # type: (TestScheduleEntry) -> TaskSetResults
        """
        Run tasks from a schedule entry one by one - entry contains just a single task.

        :param libs.test_schedule.TestScheduleEntry schedule_entry: Test schedule entry.
        :rtype: TaskSetResults
        """

        recipe_set = schedule_entry.recipe_set
        tasks = recipe_set.find_all('task')

        schedule_entry.info('running single task')

        # _run_task_set will need these, to make tasks feel like home
        recipe_set_attrs = recipe_set.attrs
        recipe_attrs = recipe_set.find_all('recipe')[0].attrs

        return self._run_task_set(schedule_entry, tasks, recipe_attrs, recipe_set_attrs)

    def _run_schedule_entry_isolated(self, schedule_entry):
        # type: (TestScheduleEntry) -> TaskSetResults
        """
        Run tasks from a schedule entry one by one, isolated from each other by restoring a base snapshot
        of the guest before running new task.

        :param libs.test_schedule.TestScheduleEntry schedule_entry: Test schedule entry.
        :rtype: TaskSetResults
        """

        if len(schedule_entry.recipe_set.find_all('task')) == 1:
            return self._run_schedule_entry_isolated_single(schedule_entry)

        return self._run_schedule_entry_isolated_snapshots(schedule_entry)

    def _run_schedule_entry_whole(self, schedule_entry):
        # type: (TestScheduleEntry) -> TaskSetResults
        """
        Run tasks from a schedule entry in a "classic" manner, running one by one
        on the same box.

        :param libs.test_schedule.TestScheduleEntry schedule_entry: Test schedule entry.
        :rtype: TaskSetResults
        """

        recipe_set = schedule_entry.recipe_set
        task_set = recipe_set.find_all('task')

        schedule_entry.info('running {} tasks in the same environment, one by one'.format(len(task_set)))

        return self._run_task_set(schedule_entry, task_set, recipe_set.find_all('recipe')[0].attrs, recipe_set.attrs)

    def _set_schedule_entry_result(self, schedule_entry):
        # type: (TestScheduleEntry) -> None
        """
        Try to find at least one task that didn't complete or didn't pass.
        """

        schedule_entry.debug('try to find any non-PASS task')

        for _, task_runs in schedule_entry.recipe_set_results.tasks.iteritems():
            for task_run in task_runs:
                run_results = task_run.results

                schedule_entry.debug("    Status='{}', Result='{}'".format(run_results['bkr_status'],
                                                                           run_results['bkr_result']))

                if run_results['bkr_status'].lower() == 'completed' and run_results['bkr_result'].lower() == 'pass':
                    continue

                schedule_entry.debug('      We have our traitor!')
                schedule_entry.result = TestScheduleResult.FAILED
                return

        schedule_entry.result = TestScheduleResult.PASSED

    def run_test_schedule_entry(self, schedule_entry):
        # type: (TestScheduleEntry) -> None
        """
        Run tasks from a schedule entry.

        :param gluetool_modules.libs.test_schedule.TestScheduleEntry schedule_entry: Test schedule entry.
        :rtype: TaskSetResults
        """

        snapshots = False

        if schedule_entry.testing_environment:
            assert schedule_entry.testing_environment
            snapshots = schedule_entry.testing_environment.snapshots

        if self.option('results-directory-template') and snapshots:
            raise GlueError('Cannot use --results-directory-template with snapshots.')

        if snapshots:
            self.info('Will run recipe set tasks serially, using snapshots')

        else:
            self.info('Will run recipe set tasks serially, without snapshots')

        if schedule_entry.runner_capability != 'restraint':
            self.overloaded_shared('run_test_schedule_entry', schedule_entry)
            return

        assert schedule_entry.guest is not None

        self.shared('trigger_event', 'test-schedule-runner-restraint.schedule-entry.started',
                    schedule_entry=schedule_entry)

        # This makes situation easier - I decided to limit number of <recipe/>
        # elements inside <recipeSet/> to exactly one. I don't know what options
        # would make Beaker XML to have more recipes inside recipeSet, and I want
        # to find out, but for the proof of concept, this makes my living easy
        # to bear.
        assert len(schedule_entry.recipe_set.find_all('recipe')) == 1

        if schedule_entry.guest.supports_snapshots and snapshots:
            results = self._run_schedule_entry_isolated(schedule_entry)

        else:
            results = self._run_schedule_entry_whole(schedule_entry)

        schedule_entry.recipe_set_results = results

        log_dict(schedule_entry.debug, 'results', results.tasks)

        self._set_schedule_entry_result(schedule_entry)

        self.shared('trigger_event', 'test-schedule-runner-restraint.schedule-entry.finished',
                    schedule_entry=schedule_entry)

    def serialize_test_schedule_entry_results(self, schedule_entry, test_suite):
        # type: (TestScheduleEntry, Any) -> None

        if schedule_entry.runner_capability != 'restraint':
            self.overloaded_shared('serialize_test_schedule_entry_results', schedule_entry, test_suite)
            return

        if self.glue.has_shared('beah_xunit_serialize'):
            self.glue.shared(
                'beah_xunit_serialize',
                test_suite,
                None,
                payload=_to_builtins(schedule_entry.recipe_set_results)
            )

        else:
            schedule_entry.warn("To serialize to xUnit format, 'beah_xunit_serialize' shared function is required",
                                sentry=True)
