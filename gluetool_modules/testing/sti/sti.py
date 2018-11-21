import glob
import tempfile
import os
import re
import sys

from concurrent.futures import ThreadPoolExecutor
import inotify.adapters
import six

import gluetool
from gluetool import GlueError, SoftGlueError
from gluetool.utils import Command, log_blob

import libci.results
from libci.sentry import PrimaryTaskFingerprintsMixin

from gluetool_modules.libs.testing_environment import TestingEnvironment


# Check whether Ansible finished running tests every 5 seconds.
DEFAULT_WATCH_TIMEOUT = 5


class NoTestAvailableError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task):
        super(NoTestAvailableError, self).__init__(task, 'No tests provided for the component')

    # do not send this entry to Sentry
    @property
    def submit_to_sentry(self):
        return False


class StiTestResult(libci.results.TestResult):
    """ STI test result data container """

    def __init__(self, glue, overall_result, result_details, **kwargs):
        super(StiTestResult, self).__init__(glue, 'functional', overall_result, **kwargs)
        self.payload = result_details


class Sti(gluetool.Module):
    """
    This modules provisions a guest, according to the given artifact in the pipeline, and runs tests
    in the Standard Test Interface format from the corresponding dist-git repository of the artifact.
    The dist-git repository is resolved from the 'dist_git_repository' shared function.

    The module can also execute a specific testing playbook(s), skipping the retrieval from dist-git.
    See the ``--playbook`` option for more information.

    The module runs Ansible in a separate thread to provide a better user experience while running the test.

    For more information about Standard Test Interface see:

        `<https://fedoraproject.org/wiki/CI/Standard_Test_Interface>`

_   """

    name = 'sti'
    description = 'Run Standard Test Interface tests on the provisioned guests'
    options = [
        ('Execution', {
            'watch-timeout': {
                'help': 'Check whether Ansible finished running tests every N seconds. (default: %(default)s)',
                'type': int,
                'default': DEFAULT_WATCH_TIMEOUT
            }
        }),
        ('Testing', {
            'arch': {
                'help': 'Architecture of the guest where the tests will be executed.'
            },
            'playbook': {
                'help': 'Use the given ansible playbook(s) for execution, skip dist-git retrieval.',
                'metavar': 'PLAYBOOK',
                'action': 'append'
            },
            'skip-guest-setup': {
                'help': 'Skip guest(s) setup',
                'action': 'store_true'
            }
        }),
    ]

    required_options = ('arch',)

    def get_playbooks_from_dist_git(self, workdir, repository):
        """
        Clone dist-git repository and return list of STI playbooks (tests) from dist-git.

        :param str workdir: Directory where dist-git will be downloaded to.
        :param DistGitRepository repository: Directory where dist-git will be downloaded to.
        """
        # clone the dist-git repository
        git_clone_command = [
            "git", "clone", "-b", repository.branch,
            "--depth", "1",
            repository.url,
            workdir
        ]

        try:
            self.info('cloning dist-git repo')
            Command(git_clone_command).run()
        except gluetool.GlueCommandError as exc:
            raise GlueError('Could not clone git repository: {}'.format(exc.output.stderr))

        # check for playbooks (tests)
        playbooks = glob.glob('{}/tests/tests*.yml'.format(workdir))
        if not playbooks:
            raise NoTestAvailableError(self.shared('primary_task'))

        return playbooks

    def _publish_results(self, overall_result, result_details):
        """ Make test results available """
        libci.results.publish_result(self, StiTestResult, overall_result, result_details)

    def _check_test_log(self, test_log_filename):
        """ Check test log for detailed results """
        results = {}
        try:
            with open(test_log_filename) as test_log:
                self.debug('Checking results in {}'.format(test_log_filename))
                for line in test_log:
                    try:
                        result, name = re.match('([^ ]+) (.*)', line).groups()
                    except AttributeError:
                        continue
                    results[name] = result
        except IOError:
            self.debug('Unable to check results in {}'.format(test_log_filename))
        return results

    def execute(self):
        self.require_shared('provision', 'run_playbook', 'detect_ansible_interpreter')

        guests = self.shared('provision', TestingEnvironment(arch=self.option('arch'), compose=None))

        # setup guests
        if not self.option('skip-guest-setup'):
            guests[0].setup()

        # get playbooks (tests) from command-line or dist-git
        playbooks = gluetool.utils.normalize_path_option(self.option('playbook'))

        if playbooks:
            workdir_prefix = 'sti-adhoc-'
        else:
            try:
                self.require_shared('dist_git_repository')
                repository = self.shared('dist_git_repository')

            except GlueError as exc:
                raise GlueError('Could not locate dist-git repository: {}'.format(exc))

            workdir_prefix = 'sti-{}-{}-'.format(repository.package, repository.branch)

        # create a working directory for running with a reasonable prefix
        workdir = tempfile.mkdtemp(dir=os.getcwd(), prefix=workdir_prefix)
        self.info("working directory '{}'".format(workdir))

        # get playbooks from dist-git, note that repository was already resolved above
        if not playbooks:
            playbooks = self.get_playbooks_from_dist_git(workdir, repository)

        gluetool.utils.log_dict(self.info, 'playbooks to execute', playbooks)

        # try to detect ansible interpreter
        interpreters = self.shared('detect_ansible_interpreter', guests[0])

        # inventory file contents
        inventory_content = """
[localhost]
sut     ansible_host={} ansible_user=root {}
""".format(guests[0].hostname, 'ansible_python_interpreter={}'.format(interpreters[0]) if interpreters else '')

        with tempfile.NamedTemporaryFile(delete=False, dir=workdir, prefix='inventory-') as inventory:

            log_blob(self.info, 'using inventory', inventory_content)
            inventory.write(inventory_content)
            inventory.flush()

        # once we support paralelization of tests*.yml execution, this will be different for each thread
        artifact_dir = tempfile.mkdtemp(prefix='tests-', dir=workdir)

        def _run_playbook_wrapper():
            self.info('starting execution of tests')

            return self.shared(
                'run_playbook', playbooks, guests, inventory=inventory.name, cwd=artifact_dir,
                variables={
                    'artifacts': artifact_dir,
                    'ansible_ssh_common_args': ' '.join(['-o ' + option for option in guests[0].options]),
                })

        # monitor artifact directory
        notify = inotify.adapters.Inotify()
        notify.add_watch(artifact_dir)

        # initial values
        run_tests = []

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
                    try:
                        result, testname = re.match(testname_regex, filename).groups()
                    except AttributeError:
                        continue

                    # do not log the test multiple times
                    if testname not in run_tests:
                        run_tests.append(testname)
                        self.info("{} - {}".format(testname, result))

                # handle end of execution
                if future.done():
                    break

        # parse results
        results = self._check_test_log(os.path.join(artifact_dir, 'test.log'))
        try:
            future.result()

        except GlueError as exc:
            if exc.__class__.__name__ == 'PlaybookError':
                # pylint: disable=no-member
                raise GlueError('Execution of ansible failed, json output follows:\n{}'.format(
                    exc.ansible_output.stdout))

            # STI defines that Ansible MUST fail if any of the tests fail
            # To differentiate from a generic ansible error, we check if
            # required test.log was generated with at least one result
            if not results:
                six.reraise(*sys.exc_info())

            self._publish_results('FAIL', results)
            self.warn('some of the tests failed :/')
            return

        self._publish_results('PASS', results)
        self.info('all tests passed \\o/')
