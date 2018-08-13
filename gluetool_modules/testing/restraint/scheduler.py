import shlex

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

    def __init__(self, task):
        # pylint: disable=line-too-long
        arches = task.task_arches.arches

        message = 'Task does not have any testable artifact - {} arches are not supported'.format(', '.join(arches))

        super(NoTestableArtifactsError, self).__init__(task, message)


class RestraintScheduler(gluetool.Module):
    """
    Prepares "schedule" for runners. It uses workflow-tomorrow - with options
    provided by the user - to prepare a list of recipe sets, then it acquire
    required number of guests, and hands this to whoever will actually run
    the recipe sets.
    """

    name = 'restraint-scheduler'
    description = 'Prepares "schedule" for runners of restraint.'

    options = {
        'arch-compatibility-map': {
            'help': """
                    Mapping between artifact arches and the actual arches we can use to test them (e.g. i686
                    can be tested on both x86_64 and i686 boxes.
                    """,
            'metavar': 'FILE',
            'default': None
        },
        'unsupported-arches': {
            'help': 'List of arches not supported by system pool.',
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

    def create_schedule(self, job_desc, image):
        """
        Main workhorse - given the job XML, get some guests, and create pairs
        (guest, tasks) for runner to process.
        """

        gluetool.log.log_blob(self.debug, 'full job description', job_desc.prettify(encoding='utf-8'))

        self._schedule = []

        recipe_sets = job_desc.find_all('recipeSet')

        self.info('job contains {} recipe sets, asking for guests'.format(len(recipe_sets)))

        # get corresponding number of guests
        guests = self.shared('provision', count=len(recipe_sets), image=image)

        if guests is None:
            raise GlueError('No guests found. Did you run a guests provider module, e.g. openstack?')

        log_dict(self.debug, 'guests', guests)
        log_dict(self.debug, 'recipe_sets', recipe_sets)

        assert len(guests) == len(recipe_sets)

        # there are tags that make not much sense for restraint - we'll filter them out
        def _remove_tags(recipe_set, name):
            self.debug("removing tags '{}'".format(name))

            for tag in recipe_set.find_all(name):
                tag.decompose()

        setup_threads = []

        for guest, recipe_set in zip(guests, recipe_sets):
            self.debug('guest: {}'.format(str(guest)))
            self.debug('recipe set:\n{}'.format(recipe_set.prettify(encoding='utf-8')))

            # remove tags we want to filter out
            for tag in ('distroRequires', 'hostRequires', 'repos', 'partitions'):
                _remove_tags(recipe_set, tag)

            self.debug('final recipe set:\n{}'.format(recipe_set.prettify(encoding='utf-8')))

            self._schedule.append((guest, recipe_set))

            # setup guest
            thread_name = 'setup-guest-{}'.format(guest.name)
            thread = utils.WorkerThread(guest.logger,
                                        guest.setup,
                                        name=thread_name)
            setup_threads.append(thread)

            thread.start()
            self.debug("setup thread '{}' started".format(thread_name))

        self.info('waiting for all guests to finish their initial setup')
        for thread in setup_threads:
            thread.join()

        if any((isinstance(thread.result, Exception) for thread in setup_threads)):
            self.error('At least one guest setup failed')
            self.error('Note: see detailed exception in debug log for more information')

            # This is strange - we can have N threads, M of them failed with an exception, but we can
            # kill pipeline only with a single one. They are all logged, though, so there should not
            # be any loss of information. Not having a better idea, let's kill pipeline with the
            # first custom exception we find.

            def _raise_first(check):
                # how to find the first item in the list: create a generator returning only those items
                # that match a condition (by calling check()), and calling next() on it will return
                # its first item (or None, in this case).
                error = next((thread.result for thread in setup_threads if check(thread.result)), None)

                if error is None:
                    return

                raise error

            # Soft errors have precedence - the let user know something bad happened, which is better
            # than just "infrastructure error".
            _raise_first(lambda result: isinstance(result, SoftGlueError))

            # Then common CI errors
            _raise_first(lambda result: isinstance(result, GlueError))

            # Ok, no custom exception, maybe just some Python ones - kill the pipeline.
            raise GlueError('At least one guest setup failed')

        self.debug('Schedule:')
        for guest, recipe_set in self._schedule:
            gluetool.log.log_blob(self.debug, str(guest), recipe_set.prettify(encoding='utf-8'))

    def execute(self):
        self.require_shared('primary_task', 'restraint', 'image')

        image = self.shared('image')
        if image is None:
            raise GlueError('No image found.')

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
        jobs = self._run_wow()

        if len(jobs) > 1:
            raise GlueError('Multiple planned wow jobs are not supported')

        job = jobs[0]

        self.debug('job as planned by wow:\n{}'.format(job.prettify(encoding='utf-8')))

        self.create_schedule(job, image)
