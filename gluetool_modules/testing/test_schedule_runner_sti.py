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
from gluetool.log import format_blob, log_blob, log_dict

from gluetool_modules.libs.test_schedule import TestScheduleResult

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import cast, Any, Callable, Dict, List, Optional, Tuple  # noqa
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

    Plugin for the "test schedule" workflow.
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

    shared_functions = ['run_test_schedule_entry', 'serialize_test_schedule_entry_results']

    def _set_schedule_entry_result(self, schedule_entry):
        # type: (TestScheduleEntry) -> None
        """
        Try to find at least one task that didn't complete or didn't pass.
        """

        self.debug('Try to find any non-PASS task')

        for task_run in schedule_entry.results:
            schedule_entry, task, result = task_run.schedule_entry, task_run.name, task_run.result

            schedule_entry.debug('  {}: {}'.format(task, result))

            if result.lower() == 'pass':
                continue

            schedule_entry.debug('    We have our traitor!')
            schedule_entry.result = TestScheduleResult.FAILED
            return

        schedule_entry.result = TestScheduleResult.PASSED

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

    def _run_playbook(self, schedule_entry, work_dirpath, artifact_dirpath, inventory_filepath):
        # type: (TestScheduleEntry, str, str, str) -> List[TaskRun]
        """
        Run an STI playbook, observe and report results.
        """

        def _run_playbook_wrapper():
            # type: () -> Any

            assert schedule_entry.guest is not None

            # `run_playbook` always returns a process output, no need to catch the exception an extract the output
            output, _ = self.shared(
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

            log_filepath = os.path.join(work_dirpath, 'ansible-output.txt')
            log_location = self.shared('artifacts_location', log_filepath, logger=schedule_entry.logger)

            with open(log_filepath, 'w') as f:
                def _write(label, s):
                    # type: (str, str) -> None

                    f.write('{}\n{}\n\n'.format(label, s))

                _write('# STDOUT:', format_blob(output.stdout))
                _write('# STDERR:', format_blob(output.stderr))

                f.flush()

            schedule_entry.info('Ansible logs are in {}'.format(log_location))

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

    def run_test_schedule_entry(self, schedule_entry):
        # type: (TestScheduleEntry) -> None

        if schedule_entry.runner_capability != 'sti':
            self.overloaded_shared('run_test_schedule_entry', schedule_entry)
            return

        self.require_shared('run_playbook', 'detect_ansible_interpreter')

        self.shared('trigger_event', 'test-schedule-runner-sti.schedule-entry.started',
                    schedule_entry=schedule_entry)

        # We don't need the working directory actually - we need artifact directory, which is
        # a subdirectory of working directory. But one day, who knows...
        work_dirpath, artifact_dirpath, inventory_filepath = self._prepare_environment(schedule_entry)

        results = self._run_playbook(schedule_entry, work_dirpath, artifact_dirpath, inventory_filepath)

        schedule_entry.results = results

        log_dict(schedule_entry.debug, 'results', results)

        self._set_schedule_entry_result(schedule_entry)

        self.shared('trigger_event', 'test-schedule-runner-sti.schedule-entry.finished',
                    schedule_entry=schedule_entry)

    # pylint: disable=invalid-name
    def serialize_test_schedule_entry_results(self, schedule_entry, test_suite):
        # type: (TestScheduleEntry, Any) -> None

        if schedule_entry.runner_capability != 'sti':
            self.overloaded_shared('serialize_test_schedule_entry_results', schedule_entry, test_suite)
            return

        # So far, nothing to do here
