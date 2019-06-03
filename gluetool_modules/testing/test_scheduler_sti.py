import glob
import os.path

import gluetool
from gluetool import GlueError, SoftGlueError

from libci.sentry import PrimaryTaskFingerprintsMixin

from gluetool_modules.libs.testing_environment import TestingEnvironment
from gluetool_modules.libs.test_schedule import TestSchedule, TestScheduleEntry as BaseTestScheduleEntry

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import Any, List, Optional  # noqa


class NoTestAvailableError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task):
        # type: (Any) -> None

        super(NoTestAvailableError, self).__init__(task, 'No tests provided for the component')

    # do not send this entry to Sentry
    @property
    def submit_to_sentry(self):
        # type: () -> bool

        return False


class TestScheduleEntry(BaseTestScheduleEntry):
    # pylint: disable=too-few-public-methods

    def __init__(self, logger, playbook_filepath):
        # type: (gluetool.log.ContextAdapter, str) -> None
        """
        Test schedule entry, suited for use with STI runners.

        :param logger: logger used as a parent of this entry's own logger.
        :param str playbook_filepath: path to a STI-compatible playbook.
        """

        # Let the ID be playbook's subpath with regard to the current directory - it's much shorter,
        # it doesn't make much sense to print its parents like Jenkins' workdir and so on.
        se_id = os.path.relpath(playbook_filepath)

        super(TestScheduleEntry, self).__init__(
            logger,
            se_id,
            'sti'
        )

        self.playbook_filepath = playbook_filepath
        self.results = None  # type: Any

    def log(self, log_fn=None):
        # type: (Optional[gluetool.log.LoggingFunctionType]) -> None

        log_fn = log_fn or self.debug

        super(TestScheduleEntry, self).log(log_fn=log_fn)

        log_fn('playbook path: {}'.format(self.playbook_filepath))


class TestSchedulerSTI(gluetool.Module):
    """
    Creates test schedule entries for ``test-scheduler`` module by inspecting STI configuration.

    By default, attempts to find all Ansible playbooks as defined by Standard Test Interface format,
    in the dist-git repository of the artifact. For access to the repository, ``dist_git_repository``
    shared function is used.

    The module can also execute a specific testing playbook(s), skipping the retrieval from dist-git.
    See the ``--playbook`` option for more information.

    For more information about Standard Test Interface see:

        `<https://fedoraproject.org/wiki/CI/Standard_Test_Interface>`

    Plugin for the "test schedule" workflow.
    """

    name = 'test-scheduler-sti'
    description = 'Create test schedule entries for ``test-scheduler`` module by inspecting STI configuration.'
    options = {
        'playbook': {
            'help': 'Use the given ansible playbook(s) for execution, skip dist-git retrieval.',
            'metavar': 'PLAYBOOK',
            'action': 'append'
        }
    }

    shared_functions = ['create_test_schedule']

    def _playbooks_from_dist_git(self, repodir):
        # type: (str) -> List[str]
        """
        Return STI playbooks (tests) from dist-git.

        :param str repodir: clone of a dist-git repository.
        """

        playbooks = glob.glob('{}/tests/tests*.yml'.format(repodir))

        if not playbooks:
            raise NoTestAvailableError(self.shared('primary_task'))

        return playbooks

    def create_test_schedule(self, testing_environment_constraints=None):
        # type: (Optional[List[TestingEnvironment]]) -> TestSchedule
        """
        Create a test schedule based on either content of artifact's dist-git repository,
        or using playbooks specified via ``--playbook`` option.

        :param list(gluetool_modules.libs.testing_environment.TestingEnvironment) testing_environment_constraints:
            limitations put on us by the caller. In the form of testing environments - with some fields possibly
            left unspecified - the list specifies what environments are expected to be used for testing.
            At this moment, only ``arch`` property is obeyed.
        :returns: a test schedule consisting of :py:class:`TestScheduleEntry` instances.
        """

        # get playbooks (tests) from command-line or dist-git
        if self.option('playbook'):
            playbooks = gluetool.utils.normalize_path_option(self.option('playbook'))

        else:
            try:
                self.require_shared('dist_git_repository')

                repository = self.shared('dist_git_repository')

            except GlueError as exc:
                raise GlueError('Could not locate dist-git repository: {}'.format(exc))

            repodir = repository.clone(
                logger=self.logger,
                prefix='dist-git-{}-{}-'.format(repository.package, repository.branch)
            )

            playbooks = self._playbooks_from_dist_git(repodir)

        gluetool.log.log_dict(self.info, 'creating schedule for {} playbooks'.format(len(playbooks)), playbooks)

        schedule = TestSchedule()

        # For each playbook, architecture and compose, create a schedule entry

        # There should be a generic "on what composes should I test this?" module - this is too
        # beaker-ish. Future patch will clean this.
        distros = self.shared('distro')

        for playbook in playbooks:
            for distro in distros:
                if not testing_environment_constraints:
                    # One day, we have to teach test-scheduler to expand this "ANY" to a list of arches.
                    self.warn('STI scheduler does not support open constraints', sentry=True)
                    continue

                for tec in testing_environment_constraints:
                    schedule_entry = TestScheduleEntry(gluetool.log.Logging.get_logger(), playbook)

                    if tec.arch == tec.ANY:
                        self.warn('STI scheduler does not support open constraints', sentry=True)
                        continue

                    schedule_entry.testing_environment = TestingEnvironment(
                        compose=distro,
                        arch=tec.arch
                    )

                    schedule.append(schedule_entry)

        schedule.log(self.debug, label='complete schedule')

        return schedule
