import collections
import tempfile
import os
import re
import sys

from concurrent.futures import ThreadPoolExecutor
import inotify.adapters
import six

import gluetool
from gluetool import GlueError
from gluetool.log import log_blob, log_dict

import libci.results

from gluetool_modules.libs.test_schedule import TestScheduleEntryStage, TestScheduleEntryState

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import Any, Callable, Dict, List, Optional, Tuple  # noqa
from gluetool_modules.testing.test_scheduler_sti import TestScheduleEntry  # noqa


# Check whether Ansible finished running tests every 5 seconds.
DEFAULT_WATCH_TIMEOUT = 5


#: Represents a single run of a test - one STI playbook can contain multiple such tests
#  - and results of this run.
#:
#: :ivar str name: name of the test.
#: :ivar libs.test_schedule.TestScheduleEntry schedule_entry: test schedule entry the task belongs to.
#: :ivar dict results: results of the test run, as reported by Ansible playbook log.
TaskRun = collections.namedtuple('TaskRun', ('name', 'schedule_entry', 'result'))


class StiTestResult(libci.results.TestResult):
    """ STI test result data container """

    def __init__(self, glue, overall_result, result_details, **kwargs):
        # type: (gluetool.Glue, str, List[TaskRun], **Any) -> None

        super(StiTestResult, self).__init__(glue, 'functional', overall_result, **kwargs)

        self.payload = result_details


def gather_test_results(schedule_entry, test_log_filename):
    # type: (TestScheduleEntry, str) -> List[TaskRun]
    """
    Extract detailed test results from test log.
    """

    results = []

    schedule_entry.debug('Checking results in {}'.format(test_log_filename))

    try:
        with open(test_log_filename) as test_log:
            for line in test_log:
                match = re.match('([^ ]+) (.*)', line)
                if not match:
                    continue

                result, name = match.groups()

                results.append(TaskRun(name=name, schedule_entry=schedule_entry, result=result))

    except IOError:
        schedule_entry.warn('Unable to check results in {}'.format(test_log_filename))

    return results


class STIRunner(gluetool.Module):
    """
    Runs STI-compatible test schedule entries.

    For more information about Standard Test Interface see:

        `<https://fedoraproject.org/wiki/CI/Standard_Test_Interface>`
    """

    name = 'test-schedule-runner-sti'
    description = 'Runs STI-compatible test schedule entries.'
    options = {
        'watch-timeout': {
            'help': 'Check whether Ansible finished running tests every SECONDS seconds. (default: %(default)s)',
            'metavar': 'SECONDS',
            'type': int,
            'default': DEFAULT_WATCH_TIMEOUT
        }
    }

    def _process_results(self, results):
        # type: (List[TaskRun]) -> str
        """
        Try to find at least one task that didn't complete or didn't pass.
        """

        self.debug('Try to find any non-PASS task')

        for task_run in results:
            schedule_entry, task, result = task_run.schedule_entry, task_run.name, task_run.result

            schedule_entry.debug('  {}: {}'.format(task, result))

            if result.lower() == 'pass':
                continue

            schedule_entry.debug('    We have our traitor!')
            return 'FAIL'

        return 'PASS'

    def _prepare_environment(self, schedule_entry):
        # type: (TestScheduleEntry) -> Tuple[str, str, str]
        """
        Prepare local environment for running the schedule entry, by setting up some directories and files.

        :returns: a path to a work directory, dedicated for this entry, and path to a "artifact" directory
            in which entry's artifacts are supposed to appear.
        """

        assert schedule_entry.guest is not None

        # Create a working directory, we try hard to keep all the related work inside this directory.
        # Under this directory, there will be an inventory file and an "artifact" directory in which
        # the Ansible is supposed to run - all artifacts created by the playbook will therefore land
        # in the artifact directory.

        work_dir_prefix = 'work-{}'.format(os.path.basename(schedule_entry.playbook_filepath))
        artifact_dir_prefix = 'tests-'

        work_dir = tempfile.mkdtemp(dir=os.getcwd(), prefix=work_dir_prefix)
        artifact_dir = tempfile.mkdtemp(dir=work_dir, prefix=artifact_dir_prefix)

        schedule_entry.info("working directory '{}'".format(work_dir))

        # try to detect ansible interpreter
        interpreters = self.shared('detect_ansible_interpreter', schedule_entry.guest)

        # inventory file contents
        ansible_interpreter = 'ansible_python_interpreter={}'.format(interpreters[0]) if interpreters else ''
        inventory_content = """
[localhost]
sut     ansible_host={} ansible_user=root {}
""".format(schedule_entry.guest.hostname, ansible_interpreter)

        with tempfile.NamedTemporaryFile(delete=False, dir=work_dir, prefix='inventory-') as inventory:
            log_blob(schedule_entry.info, 'using inventory', inventory_content)

            inventory.write(inventory_content)
            inventory.flush()

        return work_dir, artifact_dir, inventory.name

    def _run_playbook(self, schedule_entry, artifact_dirpath, inventory_filepath):
        # type: (TestScheduleEntry, str, str) -> List[TaskRun]
        """
        Run an STI playbook, observe and report results.
        """

        def _run_playbook_wrapper():
            # type: () -> Any

            assert schedule_entry.guest is not None

            return self.shared(
                'run_playbook',
                schedule_entry.playbook_filepath,
                [schedule_entry.guest],
                inventory=inventory_filepath,
                cwd=artifact_dirpath,
                json_output=False,
                variables={
                    'artifacts': artifact_dirpath,
                    'ansible_ssh_common_args': ' '.join(['-o ' + option for option in schedule_entry.guest.options])
                })

        # monitor artifact directory
        notify = inotify.adapters.Inotify()
        notify.add_watch(artifact_dirpath)

        # initial values
        run_tests = []  # type: List[str]

        # testname matching regex
        testname_regex = re.compile(r'^\.?([^_]*)_(.*).log.*$')

        # run the playbook in a separate thread
        with ThreadPoolExecutor(thread_name_prefix='testing-thread') as executor:
            future = executor.submit(_run_playbook_wrapper)

            # monitor the test execution
            while True:
                for event in notify.event_gen(yield_nones=False, timeout_s=self.option('watch-timeout')):
                    (_, event_types, path, filename) = event

                    self.debug("PATH=[{}] FILENAME=[{}] EVENT_TYPES={}".format(path, filename, event_types))

                    # we lookup testing progress by looking at their logs being created
                    if 'IN_CREATE' not in event_types:
                        continue

                    # try to match the test log name
                    match = re.match(testname_regex, filename)

                    if not match:
                        continue

                    result, testname = match.groups()

                    # do not log the test multiple times
                    if testname not in run_tests:
                        run_tests.append(testname)
                        schedule_entry.info("{} - {}".format(testname, result))

                # handle end of execution
                if future.done():
                    break

        # parse results
        results = gather_test_results(schedule_entry, os.path.join(artifact_dirpath, 'test.log'))

        try:
            future.result()

        except GlueError:
            # STI defines that Ansible MUST fail if any of the tests fail
            # To differentiate from a generic ansible error, we check if
            # required test.log was generated with at least one result
            if not results:
                six.reraise(*sys.exc_info())

        return results

    def _run_schedule_entry(self, schedule_entry):
        # type: (TestScheduleEntry) -> List[TaskRun]

        self.require_shared('run_playbook', 'detect_ansible_interpreter')

        self.shared('trigger_event', 'test-schedule-runner-sti.schedule-entry.start',
                    schedule_entry=schedule_entry)

        schedule_entry.info('starting to run tests')
        schedule_entry.stage = TestScheduleEntryStage.RUNNING
        schedule_entry.log()

        # Catch everything just to get a chance to properly update the schedule entry,
        # and re-raise the exception to continue in the natural flow of things.

        try:
            # We don't need the working directory actually - we need artifact directory, which is
            # a subdirectory of working directory. But one day, who knows...
            _, artifact_dirpath, inventory_filepath = self._prepare_environment(schedule_entry)

            results = self._run_playbook(schedule_entry, artifact_dirpath, inventory_filepath)

        # pylint: disable=broad-except
        except Exception:
            exc_info = sys.exc_info()

            schedule_entry.stage = TestScheduleEntryStage.COMPLETE
            schedule_entry.state = TestScheduleEntryState.ERROR

            self.shared('trigger_event', 'test-schedule-runner-sti.task-set.crashed',
                        schedule_entry=schedule_entry, exc_info=exc_info)

            six.reraise(*exc_info)

        schedule_entry.stage = TestScheduleEntryStage.COMPLETE
        schedule_entry.debug('finished')
        log_dict(schedule_entry.debug, 'results', results)

        self.shared('trigger_event', 'test-schedule-runner-sti.schedule-entry.finished',
                    schedule_entry=schedule_entry, results=results)

        return results

    def execute(self):
        # type: () -> None

        schedule = self.shared('test_schedule') or []

        # pylint: disable=invalid-name
        for se in schedule:
            if se.runner_capability == 'sti':
                continue

            raise GlueError("Cannot run schedule entry {}, requires '{}'".format(se.id, se.runner_capability))

        self.shared('trigger_event', 'test-schedule-runner-sti.start',
                    schedule=schedule)

        self.info('Scheduled {} items, running them one by one'.format(len(schedule)))

        results = []
        for schedule_entry in schedule:
            results.extend(self._run_schedule_entry(schedule_entry))

        overall_result = self._process_results(results)

        self.info('Result of testing: {}'.format(overall_result))

        self.shared('trigger_event', 'test-schedule-runner-sti.finished',
                    schedule=schedule, results=results)

        libci.results.publish_result(self, StiTestResult, overall_result, results)
