import shlex
import bs4

import libci
from libci import utils, CIError, SoftCIError


class NoTestAvailableError(SoftCIError):
    SUBJECT = 'No tests found for component'
    BODY = """

CI could not find any suitable tests for the component. This can have many different causes, e.g.:

    * component's configuration is incomplete, it does not provide correct test plan with tests
      for the component, or
    * the test plan is provided but it's empty, or
    * the test plan is not empty but there are filters applied in the configuration, and the result
      is an empty set of tests.

Please, see the documentation on CI configuration and what is required to correctly enable CI for
a component ([1]), current configuration ([2]), and/or consult with component's QE how to resolve
this situation.

[1] https://wiki.test.redhat.com/BaseOs/Projects/CI/Doc/UserHOWTO#EnableCIforacomponent
[2] https://gitlab.cee.redhat.com/baseos-qe/citool-config/raw/production/brew-dispatcher.yaml
    """

    def __init__(self):
        super(NoTestAvailableError, self).__init__('No tests provided for the component')


class RestraintScheduler(libci.Module):
    """
    Prepares "schedule" for runners. It uses workflow-tomorrow - with options
    provided by the user - to prepare a list of recipe sets, then it acquire
    required number of guests, and hands this to whoever will actually run
    the recipe sets.
    """

    name = 'restraint-scheduler'
    options = {
        'install-task-not-build': {
            'help': 'Try to install SUT using brew task ID as a referrence, instead of the brew build ID.',
            'action': 'store_true',
            'default': False
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

    def _run_wow(self):
        """
        Run workflow-tomorrow to create beaker job description, using options we
        got from the user.

        :returns: libci.utils.ProcessOutput with the output of w-t.
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

    def _setup_guest(self, task, guest):
        # pylint: disable=no-self-use

        """
        Run necessary command to prepare guest for following procedures.
        """

        guest.info('setting the guest up')

        guest.setup()

        # Install SUT
        self.info('installing the SUT packages')

        options = {
            'brew_method': 'install',
            'brew_tasks': '',
            'brew_builds': ''
        }

        if self.option('install-task-not-build'):
            self.debug('asked to install by task ID')

            options['brew_tasks'] = str(task.task_id)
        else:
            if task.scratch:
                self.debug('task is a scratch build - using task ID for installation')

                options['brew_tasks'] = str(task.task_id)
            else:
                self.debug('task is a regular task - using build ID for installation')

                options['brew_builds'] = str(task.build_id)

        job_xml = """
            <job>
              <recipeSet priority="Normal">
                <recipe ks_meta="method=http harness='restraint-rhts beakerlib-redhat'" whiteboard="Server">
                  <task name="/distribution/install/brew-build" role="None">
                    <params>
                      <param name="BASEOS_CI" value="true"/>
                      <param name="METHOD" value="install"/>
                      <param name="TASKS" value="{brew_tasks}"/>
                      <param name="BUILDS" value="{brew_builds}"/>
                    </params>
                    <rpm name="test(/distribution/install/brew-build)" path="/mnt/tests/distribution/install/brew-build"/>
                  </task>
                  <task name="/distribution/runtime_tests/verify-nvr-installed" role="None">
                    <params>
                      <param name="BASEOS_CI" value="true"/>
                    </params>
                    <rpm name="test(/distribution/runtime_tests/verify-nvr-installed)" path="/mnt/tests/distribution/runtime_tests/verify-nvr-installed"/>
                  </task>
                </recipe>
              </recipeSet>
            </job>
        """.format(**options)

        job = bs4.BeautifulSoup(job_xml, 'xml')

        output = self.shared('restraint', guest, job)

        if output.exit_code != 0:
            self.debug('restraint exited with invalid exit code {}'.format(output.exit_code))

            raise CIError('Installation of SUT failed, restraint reports errors')

    def create_schedule(self, task, job_desc, image):
        """
        Main workhorse - given the job XML, get some guests, and create pairs
        (guest, tasks) for runner to process.
        """

        libci.log.log_blob(self.debug, 'full job description', job_desc.prettify(encoding='utf-8'))

        self._schedule = []

        recipe_sets = job_desc.find_all('recipeSet')

        self.info('job contains {} recipe sets, asking for guests'.format(len(recipe_sets)))

        # get corresponding number of guests
        guests = self.shared('provision', count=len(recipe_sets), image=image)

        if guests is None:
            raise CIError('No guests found. Did you run a guests provider module, e.g. openstack?')
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
                                        self._setup_guest, fn_args=(task, guest,),
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
            libci.log.log_blob(self.debug, str(guest), recipe_set.prettify(encoding='utf-8'))

    def execute(self):
        if not self.has_shared('restraint'):
            raise CIError('Requires support module that would provide restraint, e.g. `restraint`.')

        if not self.has_shared('task'):
            raise CIError('Requires support module that would provide Brew task, e.g. `brew`.')

        if not self.has_shared('image'):
            raise CIError('Requires support module that would OpenStack image name, e.g. `guess-openstack-image`.')

        task = self.shared('task')
        if task is None:
            raise CIError('No brew build found.')

        image = self.shared('image')
        if image is None:
            raise CIError('No image found.')

        def _command_options(name):
            opts = self.option(name)
            if opts is None or not opts:
                return []

            return shlex.split(opts)

        # workflow-tomorrow
        wow_output = self._run_wow()

        job = bs4.BeautifulSoup(wow_output.stdout, 'xml')
        self.debug('job as planned by wow:\n{}'.format(job.prettify(encoding='utf-8')))

        self.create_schedule(task, job, image)

    def destroy(self, failure=None):
        pass
