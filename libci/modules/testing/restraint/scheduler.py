import shlex
import bs4

import libci
from libci import utils, CIError, CICommandError


class RestraintScheduler(libci.Module):
    """
    Prepares "schedule" for runners. It uses workflow-tomorrow - with options
    provided by the user - to prepare a list of recipe sets, then it acquire
    required number of guests, and hands this to whoever will actually run
    the recipe sets.
    """

    name = 'restraint-scheduler'

    options = {
        'wow-options': {
            'help': 'Additional options for workflow-tomorrow'
        }
    }

    shared_functions = ['schedule']

    _schedule = None

    def schedule(self):
        """
        Returns schedule for runners. It tells runner which recipe sets
        it should run on which guest.

        :returns: [(guest, <recipeSet/>), ...]
        """

        return self._schedule

    def _run_wow(self, task, distro, options):
        """
        Run workflow-tomorrow to create beaker job description, using options we
        got from the user.

        :param task: brew task info, as returned by `brew_task` shared function
        :param str distro: distribution to install.
        :param list options: additional options, usualy coming from wow-options option.
        :returns: libci.utils.ProcessOutput with the output of w-t.
        """

        self.info('running workflow-tomorrow to get job description')

        distro_option = ['--distro={}'.format(distro)] if distro else []

        # wow
        task_params = {
            'BASEOS_CI': 'true',
            'BASEOS_CI_COMPONENT': str(task.component)
        }

        command = [
            'bkr', 'workflow-tomorrow',
            '--dry',  # this will make wow to print job description in XML
            '--single',  # ignore multihost tests
            '--no-reserve',  # don't reserve hosts
            '--decision',  # show desicions about including/not including task in the job
            '--hardware-skip',  # ignore tasks with specific hardware requirements
            '--arch', 'x86_64',  # limit to x86_64, we're dealing with openstack - I know :(
            '--restraint',
            '--suppress-install-task'
        ] + distro_option + options

        for name, value in task_params.iteritems():
            command += ['--taskparam', '{}={}'.format(name, value)]

        try:
            return libci.utils.run_command(command)

        except CICommandError as exc:
            # Check for most common causes, and raise soft error where necessary
            soft = False

            if 'No relevant tasks found in test plan' in exc.output.stderr:
                soft = True

            raise CIError("Failure during 'wow' execution: {}".format(exc.output.stderr), soft=soft)

    def _setup_guest(self, task_id, guest):
        # pylint: disable=no-self-use

        """
        Run necessary command to prepare guest for following procedures.
        """

        guest.debug('setting the guest up')
        guest.setup(variables={
            'BREW_METHOD': 'install',
            'BREW_TASKS': str(task_id)
        })

    def create_schedule(self, task, job_desc, image):
        """
        Main workhorse - given the job XML, get some guests, and create pairs
        (guest, tasks) for runner to process.
        """

        libci.utils.log_blob(self.debug, 'full job description', job_desc.prettify())

        self._schedule = []

        recipe_sets = job_desc.find_all('recipeSet')

        self.info('job contains {} recipe sets, asking for guests'.format(len(recipe_sets)))

        # get corresponding number of guests
        guests = self.shared('provision', count=len(recipe_sets), image=image)

        assert guests is not None
        assert len(guests) == len(recipe_sets)

        # there are tags that make not much sense for restraint - we'll filter them out
        def _remove_tags(recipe_set, name):
            self.debug("removing tags '{}'".format(name))

            for tag in recipe_set.find_all(name):
                tag.decompose()

        setup_threads = []

        for guest, recipe_set in zip(guests, recipe_sets):
            self.debug('guest: {}'.format(str(guest)))
            self.debug('recipe set:\n{}'.format(recipe_set.prettify()))

            # remove tags we want to filter out
            for tag in ('distroRequires', 'hostRequires', 'repos', 'partitions'):
                _remove_tags(recipe_set, tag)

            self.debug('final recipe set:\n{}'.format(recipe_set.prettify()))

            self._schedule.append((guest, recipe_set))

            # setup guest
            thread_name = 'setup-guest-{}'.format(guest.name)
            thread = utils.WorkerThread(guest.logger,
                                        self._setup_guest, fn_args=(task.task_id, guest,),
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

            raise CIError('At least one guest setup failed')

        self.debug('Schedule:')
        for guest, recipe_set in self._schedule:
            libci.utils.log_blob(self.debug, str(guest), recipe_set.prettify())

    def execute(self):
        task = self.shared('brew_task')
        if task is None:
            raise CIError('no brew build found, did you run brew module')

        image = self.shared('image')
        if image is None:
            raise CIError('No image provided, did you run guess-*-image module?')

        distro = self.shared('distro')

        def _command_options(name):
            opts = self.option(name)
            if opts is None or not opts:
                return []

            return shlex.split(opts)

        wow_options = _command_options('wow-options')

        # workflow-tomorrow
        wow_output = self._run_wow(task, distro, wow_options)

        job = bs4.BeautifulSoup(wow_output.stdout, 'xml')
        self.debug('job as planned by wow:\n{}'.format(job.prettify()))

        self.create_schedule(task, job, image)

    def destroy(self, failure=None):
        pass
