import gluetool
import gluetool.log
from gluetool.log import log_dict, log_xml, LoggingFunctionType, LoggingWarningFunctionType

from gluetool_modules.libs.testing_environment import TestingEnvironment

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import TYPE_CHECKING, cast, Any, List, Optional  # noqa

if TYPE_CHECKING:
    import libci.guest  # noqa


class TestScheduleEntryAdapter(gluetool.log.ContextAdapter):
    def __init__(self, logger, entry_id):
        # type: (gluetool.log.ContextAdapter, str) -> None

        super(TestScheduleEntryAdapter, self).__init__(logger, {
            'ctx_schedule_entry_index': (200, entry_id)
        })


class TestScheduleEntry(object):
    # pylint: disable=too-few-public-methods

    """
    Internal representation of stuff to run, where to run and other bits necessary for scheduling
    all things the module was asked to perform.

    Follows :doc:`Test Schedule Entry Protocol </protocols/test-schedule-entry`.

    :param logger: logger used as a parent of this entry's own logger.
    :param int job_index: index of job within all jobs this entry belongs to.
    :param int recipe_set_index: index of recipe set within its job this entry belongs to.
    :param xml recipe_set: XML description of (Beaker) recipe set this entry handles.
    """

    # Logging type stubs
    #
    # These methods are added dynamically, therefore without intruducing them to mypy, every use of `self.debug`
    # would cause an error when checking types. We cannot simply set them to `None`, that makes pylint go crazy
    # because `None` is apparently not callable, and we're calling `self.debug` often :) So, we use dummy method
    # for initialization, to make pylint happy, but we wrap it with `cast` to enforce proper types to make mypy
    # happy as well :) It must a full-fledge method, because lambda cannot take keyword arguments (like sentry),
    # and pylint can discover that.
    def _fake_log_fn(self, *args, **kwargs):
        # type: (*Any, **Any) -> None

        pass

    verbose = cast(LoggingFunctionType, _fake_log_fn)
    debug = cast(LoggingFunctionType, _fake_log_fn)
    info = cast(LoggingFunctionType, _fake_log_fn)
    warn = cast(LoggingWarningFunctionType, _fake_log_fn)
    error = cast(LoggingFunctionType, _fake_log_fn)
    exception = cast(LoggingFunctionType, _fake_log_fn)

    def __init__(self, logger, job_index, recipe_set_index, recipe_set):
        # type: (gluetool.log.ContextAdapter, int, int, Any) -> None

        # pylint: disable=C0103
        self.id = 'schedule entry J#{}-RS#{}'.format(job_index, recipe_set_index)

        self.logger = TestScheduleEntryAdapter(logger, self.id)
        self.logger.connect(self)

        self.testing_environment = None  # type: Optional[TestingEnvironment]
        self.guest = None  # type: Optional[libci.guest.NetworkedGuest]
        self.package = self.recipe_set = recipe_set

    def log(self, log_fn=None):
        # type: (Optional[gluetool.log.LoggingFunctionType]) -> None

        log_fn = log_fn or self.debug

        log_fn('testing environment: {}'.format(self.testing_environment))
        log_fn('guest: {}'.format(self.guest))
        log_xml(log_fn, 'recipe set', self.recipe_set)


class TestSchedulerWow(gluetool.Module):
    """
    Create test schedule entries for ``test-scheduler`` module by calling ``beaker_job_xml`` shared function.
    """

    name = 'test-scheduler-beaker-xml'
    description = """
                  Create test schedule entries for ``test-scheduler`` module by calling ``beaker_job_xml``
                  shared function.
                  """

    shared_functions = ['create_test_schedule']

    def _log_schedule(self, label, schedule):
        # type: (str, List[TestScheduleEntry]) -> None

        self.debug('{}:'.format(label))

        for schedule_entry in schedule:
            schedule_entry.log()

    def _get_job_xmls(self, testing_environment_constraints=None):
        # type: (Optional[List[TestingEnvironment]]) -> List[Any]
        """
        Use ``beaker_job_xml`` shared function - probably running ``workflow-tomorrow`` behind the curtain - to get
        XML descriptions of Beaker jobs, implementing the testing. Provides few basic options, necessary from "system"
        point of view, the rest of the options is provided by the module behind ``beaker_job_xml``.

        :rtype: list(xml)
        :returns: A list of Beaker jobs in a form of their XML definitions.
        """

        self.info('getting Beaker job descriptions')

        log_dict(self.debug, 'given constraints', testing_environment_constraints)

        options = [
            '--single',  # ignore multihost tests
            '--no-reserve',  # don't reserve hosts
            '--hardware-skip',  # ignore tasks with specific hardware requirements
            '--restraint',
            '--suppress-install-task'
        ]

        return cast(List[Any], self.shared('beaker_job_xml', options=options, extra_context={
            'TESTING_ENVIRONMENT_CONSTRAINTS': testing_environment_constraints or []
        }))

    def _create_job_schedule(self, index, job):
        # type: (int, Any) -> List[TestScheduleEntry]
        """
        For a given job XML, extract recipe sets and their corresponding testing environments.

        :param int index: index of the ``job`` in greater scheme of things - used for logging purposes.
        :param xml job: job XML description.
        :rtype: list(ScheduleEntry)
        """

        log_xml(self.debug, 'full job description', job)

        schedule = []

        recipe_sets = job.find_all('recipeSet')

        for i, recipe_set in enumerate(recipe_sets):
            # From each recipe, extract distro and architecture, and construct testing environment description.
            # That will be passed to the provisioning modules.

            schedule_entry = TestScheduleEntry(gluetool.log.Logging.get_logger(), index, i, recipe_set)

            schedule_entry.testing_environment = TestingEnvironment(
                compose=recipe_set.find('distroRequires').find('distro_name')['value'].encode('ascii'),
                arch=recipe_set.find('distroRequires').find('distro_arch')['value'].encode('ascii')
            )

            log_xml(schedule_entry.debug, 'full recipe set', schedule_entry.recipe_set)
            log_dict(schedule_entry.debug, 'testing environment', schedule_entry.testing_environment)

            # remove tags we want to filter out
            for tag in ('distroRequires', 'hostRequires', 'repos', 'partitions'):
                schedule_entry.debug("removing tags '{}'".format(tag))

                for element in schedule_entry.recipe_set.find_all(tag):
                    element.decompose()

            log_xml(schedule_entry.debug, 'purified recipe set', schedule_entry.recipe_set)

            schedule.append(schedule_entry)

        self._log_schedule('job #{} schedule'.format(index), schedule)

        return schedule

    def _create_jobs_schedule(self, jobs):
        # type: (List[Any]) -> List[TestScheduleEntry]
        """
        Create schedule for given set of jobs.

        :param list(xml) jobs: List of jobs - in their XML representation, as generated
            by ``workflow-tomorrow`` - to schedule.
        :rtype: list(TestScheduleEntry)
        """

        self.info('creating schedule for {} jobs'.format(len(jobs)))

        schedule = []  # type: List[TestScheduleEntry]

        # for each job, create a schedule entries for its recipe sets, and put them all on one pile
        for i, job in enumerate(jobs):
            schedule += self._create_job_schedule(i, job)

        self._log_schedule('complete schedule', schedule)

        return schedule

    def create_test_schedule(self, testing_environment_constraints=None):
        # type: (Optional[List[TestingEnvironment]]) -> List[TestScheduleEntry]
        """
        Create a test schedule based on call of ``beaker_job_xml`` shared function. XML job description
        is split into recipes, each is packed into one schedule entry.

        :param list(gluetool_modules.libs.testing_environment.TestingEnvironment) testing_environment_constraints:
            limitations put on us by the caller. In the form of testing environments - with some fields possibly
            left unspecified - the list specifies what environments are expected to be used for testing.
            At this moment, only ``arch`` property is obeyed.
        :returns: a test schedule - a list of test schedule entries as described
            in :doc:`Test Schedule Entry Protocol </protocols/test-schedule-entry`.
        """

        self.require_shared('beaker_job_xml')

        job_xmls = self._get_job_xmls(testing_environment_constraints=testing_environment_constraints)

        return self._create_jobs_schedule(job_xmls)
