import shlex
import sys

import concurrent.futures

from six import reraise

import gluetool
from gluetool import utils, GlueError, SoftGlueError
from gluetool.log import log_dict
from libci.sentry import PrimaryTaskFingerprintsMixin


class NoTestableArtifactsError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    """
    Raised when the artifact we're given to test contains no usable RPMS we could actually test.
    E.g. when the artifact was build for arch A only, while our backend can handle just arches
    B and C.

    .. note::

       Now it's tightly coupled with our OpenStack backend, we cannot use our restraint modules
       e.g. in Beaker - yet. Hence the explicit list of supported arches in the message.
    """

    def __init__(self, task, supported_arches):
        # pylint: disable=line-too-long
        self.task_arches = task.task_arches.arches
        self.supported_arches = supported_arches

        message = 'Task does not have any testable artifact - {} arches are not supported'.format(', '.join(self.task_arches))  # Ignore PEP8Bear

        super(NoTestableArtifactsError, self).__init__(task, message)


class RestraintScheduler(gluetool.Module):
    """
    Prepares "test schedule" for other modules to perform. A schedule is a list of "test schedule entries"
    (see :doc:`Test Schedule Entry Protocol </protocols/test-schedule-entry>`). To create the schedule,
    supporting modules are required, to extract test plans and package necessary information into
    test schedule entries. This module then provisions and sets up the necessary guests.

    Schedule creation has following phases:

        * test schedule entries are obtained by calling ``create_test_schedule`` shared function;
        * for every testing environment, a guest is provisioned (processes all environments in parallel);
        * each guest is set up by calling ``setup_guest`` shared function indirectly (processes all guests
          in parallel as well).
    """

    name = 'test-scheduler'
    description = 'Prepares "test schedule" for other modules to perform.'

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

    shared_functions = ['test_schedule']

    _schedule = None

    @utils.cached_property
    def unsupported_arches(self):
        return utils.normalize_multistring_option(self.option('unsupported-arches'))

    @utils.cached_property
    def arch_compatibility_map(self):
        if not self.option('arch-compatibility-map'):
            return {}

        return utils.load_yaml(self.option('arch-compatibility-map'), logger=self.logger)

    def test_schedule(self):
        """
        Returns schedule for runners. It tells runner which recipe sets
        it should run on which guest.

        :returns: [(guest, <recipeSet/>), ...]
        """

        return self._schedule

    def _log_schedule(self, label, schedule):
        self.debug('{}:'.format(label))

        for schedule_entry in schedule:
            schedule_entry.log()

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
            # This is necessary - the output would tie the thread and the schedule entry in
            # the output. Modules used to actually provision the guest use their own module
            # loggers, therefore there's no connection between these two entities in the output
            # visible to the user with INFO+ loglevel.
            #
            # I don't like this line very much, it's way too similar to the most common next message:
            # usualy the ``provision`` shared function emits log message of form 'provisioning guest
            # for environment ...', but it's lesser of two evils. The proper solution would be propagation
            # of schedule_entry.logger down the stream for ``provision`` shared function to use. Leaving
            # that as an exercise for long winter evenings...
            schedule_entry.info('starting guest provisioning thread')

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
                    schedule_entry.info('provisioning of guest finished')

                    schedule_entry.guest = future.result()[0]

                else:
                    schedule_entry.error('provisioning of guest failed')

                    exc_info = future.exception_info()

                    # Exception info returned by future does not contain exception class while the info returned
                    # by sys.exc_info() does and all users of it expect the first item to be exception class.
                    exc_info = (exc_info[0].__class__, exc_info[0], exc_info[1])

                    errors.append((schedule_entry, exc_info))

                self.info('{} guests pending'.format(remaining_count))

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
            schedule_entry.info('starting guest setup thread')

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
                    schedule_entry.info('setup of guest finished')

                else:
                    schedule_entry.error('setup of guest failed')

                    exc_info = future.exception_info()
                    exc_info = (exc_info[0].__class__, exc_info[0], exc_info[1])

                    errors.append((schedule_entry, exc_info))

                self.info('{} guests pending'.format(remaining_count))

        if errors:
            self._handle_futures_errors(errors, 'At least one guest setup failed')

    def _assign_guests(self, schedule):
        """
        Provision, setup and assign guests for entries in a given schedule.

        :param list(TestScheduleEntry) schedule: List of test schedule entries.
        """

        self.info('assigning guests to a test schedule')

        self._log_schedule('schedule', schedule)

        # provision guests for schedule entries - use their testing environments and use provisioning modules
        self._provision_guests(schedule)
        self._log_schedule('complete schedule with guests', schedule)

        # setup guests
        self._setup_guests(schedule)

        self._log_schedule('final schedule', schedule)

    def execute(self):
        self.require_shared('primary_task', 'restraint', 'create_test_schedule')

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
            raise NoTestableArtifactsError(self.shared('primary_task'), supported_arches)

        # Call plugin to create the schedule
        #
        # Note: the link between us and the plugin, formed by giving it `unsupported_arches`,
        # doesn't feel right - it will disappear some day, right now I'm going to keep it,
        # leaving it for the next patch.
        schedule = self.shared('create_test_schedule', unsupported_arches=self.unsupported_arches)

        if not schedule:
            raise GlueError('Test schedule is empty')

        self._assign_guests(schedule)

        self._schedule = schedule
