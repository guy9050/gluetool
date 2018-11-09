import collections

import gluetool
import gluetool.log
from gluetool.log import log_dict, log_xml


#: Testing environment description.
#:
#: Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.
TestingEnvironment = collections.namedtuple('TestingEnvironment', [
    'compose',
    'arch'
])


class TestScheduleEntryAdapter(gluetool.log.ContextAdapter):
    def __init__(self, logger, entry_id):
        super(TestScheduleEntryAdapter, self).__init__(logger, {
            'ctx_schedule_entry_index': (200, entry_id)
        })


class TestScheduleEntry(object):
    """
    Internal representation of stuff to run, where to run and other bits necessary for scheduling
    all things the module was asked to perform.

    Follows :doc:`Test Schedule Entry Protocol </protocols/test-schedule-entry`.

    :param logger: logger used as a parent of this entry's own logger.
    :param int job_index: index of job within all jobs this entry belongs to.
    :param int recipe_set_index: index of recipe set within its job this entry belongs to.
    :param xml recipe_set: XML description of (Beaker) recipe set this entry handles.
    """

    def __init__(self, logger, job_index, recipe_set_index, recipe_set):
        # pylint: disable=C0103
        self.id = 'schedule entry J#{}-RS#{}'.format(job_index, recipe_set_index)

        self.logger = TestScheduleEntryAdapter(logger, self.id)
        self.logger.connect(self)

        self.testing_environment = None
        self.guest = None
        self.package = self.recipe_set = recipe_set

    def log(self, log_fn=None):
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

    shared_functions = ('create_test_schedule',)

    def _log_schedule(self, label, schedule):
        self.debug('{}:'.format(label))

        for schedule_entry in schedule:
            schedule_entry.log()

    def _get_job_xmls(self, unsupported_arches):
        """
        Use ``beaker_job_xml`` shared function - probably running ``workflow-tomorrow`` behind the curtain - to get
        XML descriptions of Beaker jobs, implementing the testing. Provides few basic options, necessary from "system"
        point of view, the rest of the options is provided by the module behind ``beaker_job_xml``.

        :rtype: list(xml)
        :returns: A list of Beaker jobs in a form of their XML definitions.
        """

        self.info('getting Beaker job descriptions')

        options = [
            '--single',  # ignore multihost tests
            '--no-reserve',  # don't reserve hosts
            '--hardware-skip',  # ignore tasks with specific hardware requirements
            '--restraint',
            '--suppress-install-task'
        ]

        # To limit to just supported architectures, using --arch=foo would work fine
        # until the testing runs into an artifact with incomplete set of arches, with
        # foo present. Configuration would try to limit recipe sets to just those arches
        # present, add --arch=foo. The scheduler would try to limit arches even more,
        # to supported ones only, adding another --arch=foo, which would make wow construct
        # *two* same recipeSets for arch foo, possibly leading to provisioning two boxes
        # for this arch, running the exactly same set of tasks.
        #
        # On the other hand, multiple --no-arch=not-foo seem to be harmless, therefore we
        # could try this approach instead. So, user must provide a list of arches not
        # supported by the backing pool, and we add --no-arch for each of them, letting wow
        # know we cannot run any tasks relevant just on those arches. It *still* may lead
        # to multiple recipeSets: e.g. if our backend supports x86_64, it supports i686
        # out of the box as well, and wow may split i686-only tasks to a separate box. But
        # this is not that harmful as the original issue.
        #
        # This is far from ideal - in the ideal world, scheduler should not have its own
        # list of unsupported, it should rely on provisioner features (what arches it can
        # and cannot schedule); but that would require each provisioner to report not just
        # supported arches, but unsupported as well, being aware of *all* existing arches,
        # which smells weird :/ Needs a bit of thinking.
        options += [
            '--no-arch={}'.format(arch) for arch in unsupported_arches
        ]

        return self.shared('beaker_job_xml', options=options)

    def _create_job_schedule(self, index, job):
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
        """
        Create schedule for given set of jobs.

        :param list(xml) jobs: List of jobs - in their XML representation, as generated
            by ``workflow-tomorrow`` - to schedule.
        :rtype: list(TestScheduleEntry)
        """

        self.info('creating schedule for {} jobs'.format(len(jobs)))

        schedule = []

        # for each job, create a schedule entries for its recipe sets, and put them all on one pile
        for i, job in enumerate(jobs):
            schedule += self._create_job_schedule(i, job)

        self._log_schedule('complete schedule', schedule)

        return schedule

    def create_test_schedule(self, unsupported_arches=None):
        """
        Create a test schedule based on call of ``beaker_job_xml`` shared function. XML job description
        is split into recipes, each is packed into one schedule entry.

        :returns: a test schedule - a list of test schedule entries as described
            in :doc:`Test Schedule Entry Protocol </protocols/test-schedule-entry`.
        """

        self.require_shared('beaker_job_xml')

        return self._create_jobs_schedule(self._get_job_xmls(unsupported_arches))
