import gluetool
import gluetool.log
from gluetool.log import log_dict, log_xml

from gluetool_modules.libs.testing_environment import TestingEnvironment
from gluetool_modules.libs.test_schedule import TestSchedule, TestScheduleEntry as BaseTestScheduleEntry

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import cast, Any, List, Optional  # noqa


class TestScheduleEntry(BaseTestScheduleEntry):
    # pylint: disable=too-few-public-methods

    def __init__(self, logger, job_index, recipe_set_index, recipe_set):
        # type: (gluetool.log.ContextAdapter, int, int, Any) -> None
        """
        Test schedule entry, suited for use with Restraint.

        :param logger: logger used as a parent of this entry's own logger.
        :param int job_index: index of job within all jobs this entry belongs to.
        :param int recipe_set_index: index of recipe set within its job this entry belongs to.
        :param xml recipe_set: XML description of (Beaker) recipe set this entry handles.
        """

        super(TestScheduleEntry, self).__init__(
            logger,
            'J#{} RS#{}'.format(job_index, recipe_set_index),
            'restraint'
        )

        self.recipe_set = recipe_set

    def log(self, log_fn=None):
        # type: (Optional[gluetool.log.LoggingFunctionType]) -> None

        log_fn = log_fn or self.debug

        super(TestScheduleEntry, self).log(log_fn=log_fn)

        log_xml(log_fn, 'recipe set', self.recipe_set)


class TestSchedulerBeakerXML(gluetool.Module):
    """
    Create test schedule entries for ``test-scheduler`` module by calling ``beaker_job_xml`` shared function.
    """

    name = 'test-scheduler-beaker-xml'
    description = """
                  Create test schedule entries for ``test-scheduler`` module by calling ``beaker_job_xml``
                  shared function.
                  """

    shared_functions = ['create_test_schedule']

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
        # type: (int, Any) -> TestSchedule
        """
        For a given job XML, extract recipe sets and their corresponding testing environments.

        :param int index: index of the ``job`` in greater scheme of things - used for logging purposes.
        :param xml job: job XML description.
        :rtype: TestSchedule
        """

        log_xml(self.debug, 'full job description', job)

        schedule = TestSchedule()

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

        schedule.log(self.debug, label='job #{} schedule'.format(index))

        return schedule

    def _create_jobs_schedule(self, jobs):
        # type: (List[Any]) -> TestSchedule
        """
        Create schedule for given set of jobs.

        :param list(xml) jobs: List of jobs - in their XML representation, as generated
            by ``workflow-tomorrow`` - to schedule.
        :rtype: TestSchedule
        """

        self.info('creating schedule for {} jobs'.format(len(jobs)))

        schedule = TestSchedule()

        # for each job, create a schedule entries for its recipe sets, and put them all on one pile
        for i, job in enumerate(jobs):
            schedule.extend(self._create_job_schedule(i, job))

        schedule.log(self.debug, label='complete schedule')

        return schedule

    def create_test_schedule(self, testing_environment_constraints=None):
        # type: (Optional[List[TestingEnvironment]]) -> TestSchedule
        """
        Create a test schedule based on call of ``beaker_job_xml`` shared function. XML job description
        is split into recipes, each is packed into one schedule entry.

        :param list(gluetool_modules.libs.testing_environment.TestingEnvironment) testing_environment_constraints:
            limitations put on us by the caller. In the form of testing environments - with some fields possibly
            left unspecified - the list specifies what environments are expected to be used for testing.
            At this moment, only ``arch`` property is obeyed.
        :returns: a test schedule - a list of :py:class:`libs.TestScheduleEntry` instances.
        """

        self.require_shared('beaker_job_xml')

        job_xmls = self._get_job_xmls(testing_environment_constraints=testing_environment_constraints)

        return self._create_jobs_schedule(job_xmls)
