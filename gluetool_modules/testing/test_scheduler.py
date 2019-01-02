import shlex
import sys

from six import reraise

import gluetool
from gluetool import utils, GlueError, SoftGlueError
from gluetool.log import log_dict
from libci.sentry import PrimaryTaskFingerprintsMixin

import gluetool_modules.libs.artifacts
from gluetool_modules.libs import ANY
from gluetool_modules.libs.jobs import Job, run_jobs, handle_job_errors
from gluetool_modules.libs.testing_environment import TestingEnvironment


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

        * scheduler prepares a set of `constraints` - what environments it is expected to run tests on;
        * test schedule entries are obtained by calling ``create_test_schedule`` shared function, which
          is given the constraints to be guided by them;
        * for every test schedule entry - and its environment - a guest is provisioned (processes all
          environments in parallel);
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
        }
    }

    shared_functions = ['test_schedule']

    _schedule = None

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

    def _provision_guests(self, schedule):
        """
        Provision guests for schedule entries.

        :param list(ScheduleEntry) schedule: Schedule to provision guests for.
        """

        self.debug('provisioning guests')

        # For each schedule entry, create a setup thread running shared function ``provision`` for
        # given testing environment.

        self.info('provisioning {} guests'.format(len(schedule)))

        # Yes, we could pass shared function ``provision`` directly to job, but that way we
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

        # Prepare list of jobs and callbacks for ``run_jobs``.
        jobs = [
            # `se` instead of `schedule_entry` to avoid binding in the inner functions bellow
            Job(logger=se.logger, target=_provision_wrapper, args=(se,), kwargs={}) for se in schedule
        ]

        # called when provisioning jobs starts
        def _before_job_start(schedule_entry):
            schedule_entry.debug('planning guest for environment: {}'.format(schedule_entry.testing_environment))

        # called when provisioning job succeeded - store returned guest in the schedul eentry
        def _on_job_complete(result, schedule_entry):
            schedule_entry.info('provisioning of guest finished')

            schedule_entry.guest = result[0]

        # called when provisioning job failed
        def _on_job_error(exc_info, schedule_entry):
            # pylint: disable=unused-argument

            schedule_entry.error('provisioning of guest failed')

        # called when provisioning job finished
        def _on_job_done(remaining_count, schedule_entry):
            # pylint: disable=unused-argument

            self.info('{} guests pending'.format(remaining_count))

        job_errors = run_jobs(
            jobs,
            logger=self.logger,
            worker_name_prefix='provision-thread',
            on_job_start=_before_job_start,
            on_job_complete=_on_job_complete,
            on_job_error=_on_job_error,
            on_job_done=_on_job_done
        )

        if job_errors:
            handle_job_errors(job_errors, 'At least one provisioning attempt failed')

    def _setup_guests(self, schedule):
        """
        Setup all guests of a schedule.

        :param list(ScheduleEntry) schedule: Schedule listing guests to set up.
        """

        self.debug('setting up the guests')

        # for each schedule entry, create a setup thread running ``guest.setup``

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

        # Prepare list of jobs and callbacks for ``run_jobs``.
        jobs = [
            Job(logger=se.logger, target=_guest_setup_wrapper, args=(se,), kwargs={}) for se in schedule
        ]

        # called when setup jobs starts
        def _before_job_start(schedule_entry):
            schedule_entry.debug('planning setup of guest: {}'.format(schedule_entry.guest))

        # called when setup job succeeded
        def _on_job_complete(result, schedule_entry):
            # pylint: disable=unused-argument

            schedule_entry.info('setup of guest finished')

        # called when setup job failed
        def _on_job_error(exc_info, schedule_entry):
            # pylint: disable=unused-argument

            schedule_entry.error('setup of guest failed')

        # called when setup job finished
        def _on_job_done(remaining_count, schedule_entry):
            # pylint: disable=unused-argument

            self.info('{} guests pending'.format(remaining_count))

        job_errors = run_jobs(
            jobs,
            logger=self.logger,
            worker_name_prefix='setup-thread',
            on_job_start=_before_job_start,
            on_job_complete=_on_job_complete,
            on_job_error=_on_job_error,
            on_job_done=_on_job_done
        )

        if job_errors:
            handle_job_errors(job_errors, 'At least one guest setup failed')

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
        self.require_shared('primary_task', 'tasks', 'restraint', 'create_test_schedule')

        def _command_options(name):
            opts = self.option(name)
            if opts is None or not opts:
                return []

            return shlex.split(opts)

        # Check whether we have *any* artifacts at all, before we move on to more fine-grained checks.
        gluetool_modules.libs.artifacts.has_artifacts(*self.shared('tasks'))

        # To create a schedule, we need to set up few constraints. So far the only known is the list of architectures
        # we'd like to see being used. For that, we match architectures present in the artifact with a list of
        # architectures provisioner can provide, and we find out what architectures we need (or cannot get...).
        # And, for example, whether there's anything left to test.
        artifact_arches = self.shared('primary_task').task_arches.arches

        provisioner_capabilities = self.shared('provisioner_capabilities')
        log_dict(self.debug, 'provisioner capabilities', provisioner_capabilities)

        supported_arches = provisioner_capabilities.available_arches if provisioner_capabilities else []

        log_dict(self.debug, 'artifact arches', artifact_arches)
        log_dict(self.debug, 'supported arches', supported_arches)

        # When provisioner's so bold that it supports *any* architecture, give him every architecture present
        # in the artifact, and watch it burn :)
        if supported_arches is ANY:
            valid_arches = artifact_arches

        else:
            valid_arches = []

            for arch in artifact_arches:
                # artifact arch is supported directly
                if arch in supported_arches:
                    valid_arches.append(arch)
                    continue

                # It may be possible to find compatible architecture, e.g. it may be fine to test
                # i686 artifacts on x86_64 boxes. Let's check the configuration.

                # Start with a list of arches compatible with `arch`.
                compatible_arches = self.arch_compatibility_map.get(arch, [])

                # Find which of these are supported.
                compatible_and_supported_arches = [
                    compatible_arch for compatible_arch in compatible_arches if compatible_arch in supported_arches
                ]

                # If there are any compatible & supported, add the original `arch` to the list of valid arches,
                # because we can test it.
                if compatible_and_supported_arches:
                    # Warning, because nothing else submits to Sentry, and Sentry because
                    # problem of secondary arches doesn't fit well with nice progress of
                    # testing environments, and I'd really like to observe the usage of
                    # this feature, without grepping all existing logs :/ If it's being
                    # used frequently, we can always silence the Sentry submission.

                    self.warn('Artifact arch {} not supported but compatible with {}'.format(
                        arch, ', '.join(compatible_and_supported_arches)
                    ), sentry=True)

                    valid_arches.append(arch)

        log_dict(self.debug, 'valid artifact arches', valid_arches)

        if not valid_arches:
            raise NoTestableArtifactsError(self.shared('primary_task'), supported_arches)

        # `noarch` is supported naturally on all other arches, so, when we encounter an artifact with just
        # the `noarch`, we "reset" the list of valid arches to let scheduler plugin know we'd like to get all
        # arches possible. But we have to be careful and take into account what provisioner told us about itself,
        # because we could mislead the scheduler plugin into thinking that every architecture is valid - if
        # provisioner doesn't support "ANY" arch, we have to prepare constraints just for the supported arches.
        # We can use all of them, true, because it's `noarch`, but we have to limit the testing to just them.
        if valid_arches == ['noarch']:
            self.debug("'noarch' is the only valid arch")

            # If provisioner boldly promised anything was possible, empty list of valid arches would result
            # into us not placing any constraints on the environments, and we should get really everything.
            if supported_arches is ANY:
                valid_arches = []

            # On the other hand, if provisioner can support just a limited set of arches, don't be greedy.
            else:
                valid_arches = supported_arches

        # When `noarch` is not the single valid arch, we should remove it from the list - provisioners cannot
        # give us `noarch` guests, and we somehow silently expect testing process to test `noarch` packages as well
        # when testing "arch" packages on given guests. Or they don't, but that fine as well - from our point
        # of view - they *could*, that's all that matter to us. We want to keep other arch constraints, however.
        elif 'noarch' in valid_arches:
            self.debug("'noarch' is not the only valid arch")

            valid_arches.remove('noarch')

        log_dict(self.debug, 'valid artifact arches (no noarch)', valid_arches)

        # Call plugin to create the schedule
        schedule = self.shared('create_test_schedule', testing_environment_constraints=[
            TestingEnvironment(arch=arch, compose=TestingEnvironment.ANY) for arch in valid_arches
        ])

        if not schedule:
            raise GlueError('Test schedule is empty')

        self._assign_guests(schedule)

        self._schedule = schedule
