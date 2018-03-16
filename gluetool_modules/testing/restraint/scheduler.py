import shlex

import gluetool
from gluetool import utils, GlueError, SoftGlueError


class RestraintScheduler(gluetool.Module):
    """
    Prepares "schedule" for runners. It uses workflow-tomorrow - with options
    provided by the user - to prepare a list of recipe sets, then it acquire
    required number of guests, and hands this to whoever will actually run
    the recipe sets.
    """

    name = 'restraint-scheduler'
    description = 'Prepares "schedule" for runners of restraint.'

    shared_functions = ['schedule']

    _schedule = None

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
            '--arch', 'x86_64',  # limit to x86_64, we're dealing with openstack - I know :(
            '--restraint',
            '--suppress-install-task'
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

        task_arches = self.shared('primary_task').task_arches
        if 'x86_64' not in task_arches.arches and 'noarch' not in task_arches.arches:
            # pylint: disable=line-too-long
            raise GlueError('Task does not have any testable artifact: only x86_64 or noarch are supported, task contains {}'.format(', '.join(task_arches.arches)))  # Ignore PEP8Bear

        # workflow-tomorrow
        jobs = self._run_wow()

        if len(jobs) > 1:
            raise GlueError('Multiple planned wow jobs are not supported')

        job = jobs[0]

        self.debug('job as planned by wow:\n{}'.format(job.prettify(encoding='utf-8')))

        self.create_schedule(job, image)
