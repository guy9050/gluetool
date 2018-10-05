import collections
import shlex
import sys

import concurrent.futures

import gluetool
from gluetool import utils, GlueError, SoftGlueError
from gluetool.log import log_dict, log_xml, ContextAdapter
from libci.sentry import PrimaryTaskFingerprintsMixin

from six import reraise


#: Testing environment description.
#:
#: Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.
TestingEnvironment = collections.namedtuple('TestingEnvironment', [
    'distro',
    'arch'
])


class NoTestableArtifactsError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    """
    Raised when the artifact we're given to test contains no usable RPMS we could actually test.
    E.g. when the artifact was build for arch A only, while our backend can handle just arches
    B and C.

    .. note::

       Now it's tightly coupled with our OpenStack backend, we cannot use our restraint modules
       e.g. in Beaker - yet. Hence the explicit list of supported arches in the message.
    """

    def __init__(self, task):
        # pylint: disable=line-too-long
        arches = task.task_arches.arches

        message = 'Task does not have any testable artifact - {} arches are not supported'.format(', '.join(arches))

        super(NoTestableArtifactsError, self).__init__(task, message)


class ScheduleEntryAdapter(ContextAdapter):
    def __init__(self, logger, job_index, recipe_set_index):
        super(ScheduleEntryAdapter, self).__init__(logger, {
            'ctx_schedule_entry_index': (200, 'schedule entry J#{}-RS#{}'.format(job_index, recipe_set_index))
        })


class ScheduleEntry(object):
    # pylint: disable=too-few-public-methods

    """
    Internal representation of stuff to run, where to run and other bits necessary for scheduling
    all things the module was asked to perform.

    :param logger: logger used as a parent of this entry's own logger.
    :param int job_index: index of job within all jobs this entry belongs to.
    :param int recipe_set_index: index of recipe set within its job this entry belongs to.
    :param xml recipe_set: XML description of (Beaker) recipe set this entry handles.
    """

    def __init__(self, logger, job_index, recipe_set_index, recipe_set):
        self.logger = ScheduleEntryAdapter(logger, job_index, recipe_set_index)
        self.logger.connect(self)

        self.recipe_set = recipe_set

        self.testing_environment = None
        self.guest = None


class RestraintScheduler(gluetool.Module):
    """
    Prepares "schedule" for other modules to perform. A schedule is a list of (Beaker-compatible) XML
    descriptions of recipes paired with guests. Following modules can then use these guests to perform
    whatever necessary to achieve results for XML prescriptions.

    Schedule creation has following phases:

        * XML descriptions of jobs are acquired by calling ``beaker_job_xml`` shared function;
        * these jobs are split to recipe sets, and each recipe set is used to extract a testing environment
          it requires for testing;
        * for every testing environment, a guest is provisioned (processes all environments in parallel);
        * each guest is set up by calling ``setup_guest`` shared function indirectly (processes all guests
          in parallel as well).
    """

    name = 'restraint-scheduler'
    description = 'Prepares "schedule" for ``restraint`` runners.'

    # pylint: disable=gluetool-option-hard-default
    options = {
        'arch-compatibility-map': {
            'help': """
                    Mapping between artifact arches and the actual arches we can use to test them (e.g. i686
                    can be tested on both x86_64 and i686 boxes (default: %(default)s).
                    """,
            'metavar': 'FILE',
            'default': None
        },
        'unsupported-arches': {
            'help': 'List of arches not supported by system pool (default: None).',
            'metavar': 'ARCH1[,ARCH2...]',
            'default': [],
            'action': 'append'
        }
    }

    required_options = ('unsupported-arches',)

    shared_functions = ['schedule']

    _schedule = None

    @utils.cached_property
    def unsupported_arches(self):
        return utils.normalize_multistring_option(self.option('unsupported-arches'))

    @utils.cached_property
    def arch_compatibility_map(self):
        if not self.option('arch-compatibility-map'):
            return {}

        return utils.load_yaml(self.option('arch-compatibility-map'), logger=self.logger)

    def schedule(self):
        """
        Returns schedule for runners. It tells runner which recipe sets
        it should run on which guest.

        :returns: [(guest, <recipeSet/>), ...]
        """

        return self._schedule

    def _run_wow(self):
        """
        Run workflow-tomorrow to create beaker job description, using options we
        got from the user.

        :returns: gluetool.utils.ProcessOutput with the output of w-t.
        """

        self.info('running workflow-tomorrow to get job description')

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
            '--no-arch={}'.format(arch) for arch in self.unsupported_arches
        ]

        return self.shared('beaker_job_xml', options=options)

    def _log_schedule(self, label, schedule):
        self.debug('{}:'.format(label))

        for schedule_entry in schedule:
            schedule_entry.debug('testing environment: {}'.format(schedule_entry.testing_environment))
            schedule_entry.debug('guest: {}'.format(schedule_entry.guest))
            log_xml(schedule_entry.debug, 'recipe set', schedule_entry.recipe_set)

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
            # That will be passed to the provisioning modules. This module does not have to know it.

            schedule_entry = ScheduleEntry(self.logger, index, i, recipe_set)

            schedule_entry.testing_environment = TestingEnvironment(
                distro=recipe_set.find('distroRequires').find('distro_name')['value'].encode('ascii'),
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

    def _handle_futures_errors(self, errors, exception_message):
        """
        Take care of reporting exceptions gathered from futures, and re-raise
        one of them - or a new, generic one - to report a phase of scheduling process failed.

        :param list(tuple(ScheduleEntry, exception info)) errors: schedule entries and their corresponding
            exceptions
        :param str exception_label: a label used for logging exceptions
        :param str exception_message: a message used when raising generic exception.
        """

        self.debug('at least one future failed')

        # filter exceptions using given ``check`` callback, and raise the first suitable one - or return back
        def _raise_first(check):
            for schedule_entry, exc_info in errors:
                if not check(exc_info):
                    continue

                schedule_entry.error('terminating schedule creation')

                reraise(*exc_info)

        # Soft errors have precedence - the let user know something bad happened, which is better
        # than just "infrastructure error".
        _raise_first(lambda exc: isinstance(exc[1], SoftGlueError))

        # Then common CI errors
        _raise_first(lambda exc: isinstance(exc[1], GlueError))

        # Ok, no custom exception, maybe just some Python ones - kill the pipeline.
        raise GlueError(exception_message)

    def _provision_guests(self, schedule):
        """
        Provision guests for schedule entries.

        :param list(ScheduleEntry) schedule: Schedule to provision guests for.
        """

        self.debug('provisioning guests')

        # for each schedule entry, create a setup thread running shared function ``provision`` for
        # given testing environment

        futures = {}
        errors = []

        self.info('provisioning {} guests'.format(len(schedule)))

        # Yes, we could pass shared function ``provision`` directly to executor, but that way we
        # wouldn't have any knowledge about the thread used to run it when we handle future exceptions.
        # All code running within the future does have thread context in the log, our error handling
        # would not because it'd be runing in the main thread, making it hard for user to debug which
        # exception originated in which thread - and hence what environment was affected.
        #
        # Using this wrapper we can log - and submit to Sentry! - the exception using schedule entry's logger
        # (which isn't new, we can do that now as well) but since we capture - and log - exceptions while
        # still in their respective threads, running to fulfill the future, we get thread name in all our log
        # entries (for free :).

        def _provision_wrapper(schedule_entry):
            try:
                return self.shared('provision', schedule_entry.testing_environment)

            # pylint: disable=broad-except
            except Exception:
                # save exc_info, to avoid spoiling it by any exception raised while logging/reporting it
                exc_info = sys.exc_info()

                schedule_entry.exception('Guest provisioning failed')

                self.glue.sentry_submit_exception(gluetool.Failure(self, exc_info), logger=schedule_entry.logger)

                # And re-raise it when we're done with it - we logged and reported the exception, by re-raising it
                # we propagate it outside of this thread, and the "global" error handling code could see her when
                # deciding what exception to use to kill the pipeline.
                reraise(*exc_info)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(schedule),
                                                   thread_name_prefix='provision-thread') as executor:
            for schedule_entry in schedule:
                schedule_entry.debug('planning guest for environment: {}'.format(schedule_entry.testing_environment))

                future = executor.submit(_provision_wrapper, schedule_entry)
                futures[future] = schedule_entry

            # If we leave context here, the rest of our code would run after all futures finished - context would
            # block in its __exit__ on executor's state.. That'd be generaly fine but we'd like to inform user about
            # our progress, and that we can do be checking futures as they complete, one by one, not waiting for the
            # last one before we start checking them. This thread *will* sleep from time to time, when there's no
            # complete future available, but that's fine. We'll get our hands on each complete one as soon as
            # possible, letting user know about the progress.

            for i, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                remaining_count = len(schedule) - i

                schedule_entry = futures[future]

                if future.exception() is None:
                    # provisioning succeeded - store returned guest in the schedule entry
                    schedule_entry.info('provisioning of guest finished, {} guests pending'.format(remaining_count))

                    schedule_entry.guest = future.result()[0]
                    continue

                schedule_entry.error('provisioning of guest failed, {} guests pending'.format(remaining_count))

                exc_info = future.exception_info()

                # Exception info returned by future does not contain exception class while the info returned
                # by sys.exc_info() does and all users of it expect the first item to be exception class.
                exc_info = (exc_info[0].__class__, exc_info[0], exc_info[1])

                errors.append((schedule_entry, exc_info))

        if errors:
            self._handle_futures_errors(errors, 'At least one provisioning attempt failed')

    def _setup_guests(self, schedule):
        """
        Setup all guests of a schedule.

        :param list(ScheduleEntry) schedule: Schedule listing guests to set up.
        """

        self.debug('setting up the guests')

        # for each schedule entry, create a setup thread running ``guest.setup``

        futures = {}
        errors = []

        self.info('setting up {} guests'.format(len(schedule)))

        def _guest_setup_wrapper(schedule_entry):
            try:
                return schedule_entry.guest.setup()

            # pylint: disable=broad-except
            except Exception:
                exc_info = sys.exc_info()

                schedule_entry.exception('Guest setup failed')

                self.glue.sentry_submit_exception(gluetool.Failure(self, exc_info), logger=schedule_entry.logger)

                # And re-raise it when we're done with it - the error handling code wants
                # to see all exceptions, and wants to raise the best one.
                reraise(*exc_info)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(schedule),
                                                   thread_name_prefix='setup-thread') as executor:
            for schedule_entry in schedule:
                schedule_entry.debug('planning setup of guest: {}'.format(schedule_entry.guest))

                future = executor.submit(_guest_setup_wrapper, schedule_entry)
                futures[future] = schedule_entry

            for i, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                remaining_count = len(schedule) - i

                schedule_entry = futures[future]

                if future.exception() is None:
                    schedule_entry.info('setup of guest finished, {} guests pending'.format(remaining_count))
                    continue

                self.error('setup of guest failed, {} guests pending'.format(remaining_count))

                exc_info = future.exception_info()
                exc_info = (exc_info[0].__class__, exc_info[0], exc_info[1])

                errors.append((schedule_entry, exc_info))

        if errors:
            self._handle_futures_errors(errors, 'At least one guest setup failed')

    def _create_jobs_schedule(self, jobs):
        """
        Create schedule for given set of jobs.

        :param list(xml) jobs: List of jobs - in their XML representation, as scheduled
            by e.g. ``workflow-tomorrow`` - to schedule.
        :rtype: list(tuple(libci.guest.Guest, xml))
        """

        self.info('creating schedule for {} jobs'.format(len(jobs)))

        schedule = []

        # for each job, create a schedule entries for its recipe sets, and put them all on one pile
        for i, job in enumerate(jobs):
            schedule += self._create_job_schedule(i, job)

        self._log_schedule('complete schedule', schedule)

        # provision guests for schedule entries - use their testing environments and use provisioning modules
        self._provision_guests(schedule)
        self._log_schedule('complete schedule with guests', schedule)

        # setup guests
        self._setup_guests(schedule)

        self._log_schedule('final schedule', schedule)

        # strip away our internal info - all our customers are interested in are recipe sets and guests
        return [
            (schedule_entry.guest, schedule_entry.recipe_set) for schedule_entry in schedule
        ]

    def execute(self):
        self.require_shared('primary_task', 'restraint')

        def _command_options(name):
            opts = self.option(name)
            if opts is None or not opts:
                return []

            return shlex.split(opts)

        # Remove any artifact arch that's also on an "unsupported arches" list. If no arch remains,
        # we have nothing to test.
        artifact_arches = self.shared('primary_task').task_arches.arches

        provisioner_capabilities = self.shared('provisioner_capabilities')
        log_dict(self.debug, 'provisioner capabilities', provisioner_capabilities)

        supported_arches = provisioner_capabilities.available_arches if provisioner_capabilities else []

        log_dict(self.debug, 'artifact arches', artifact_arches)
        log_dict(self.debug, 'supported arches', supported_arches)

        valid_arches = []
        for arch in artifact_arches:
            # artifact arch is supported
            if arch in supported_arches:
                valid_arches.append(arch)
                continue

            compatible_arches = self.arch_compatibility_map.get(arch, [])

            # there is an supported arch compatible with artifact arch
            if any([compatible_arch in supported_arches for compatible_arch in compatible_arches]):
                valid_arches.append(arch)

        log_dict(self.debug, 'valid artifact arches', valid_arches)

        if not valid_arches:
            raise NoTestableArtifactsError(self.shared('primary_task'))

        # workflow-tomorrow
        self._schedule = self._create_jobs_schedule(self._run_wow())
