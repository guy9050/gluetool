import os
import re
import shlex
import bs4

import gluetool
from gluetool import utils, GlueError, SoftGlueError
from libci.sentry import PrimaryTaskFingerprintsMixin


class SUTInstallationFailedError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task, installation_logs):
        super(SUTInstallationFailedError, self).__init__(task, 'SUT installation failed')

        self.installation_logs = installation_logs


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
        'install-task-not-build': {
            'help': 'Try to install SUT using brew task ID as a referrence, instead of the brew build ID.',
            'action': 'store_true',
            'default': False
        },
        'install-rpms-blacklist': {
            # pylint: disable=line-too-long
            'help': 'Regexp pattern (compatible with ``egrep``) - when installing build, matching packages will not be installed.',
            'type': str,
            'default': ''
        },
        'install-method': {
            'help': 'Yum method to use for installation (default: ``install``).',
            'type': str,
            'default': 'install'
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

    def _setup_guest(self, tasks, guest):
        # pylint: disable=no-self-use
        """
        Run necessary command to prepare guest for following procedures.
        """

        guest.info('setting the guest up')

        guest.setup()

        # Install SUT
        self.info('installing the SUT packages')

        options = {
            'brew_method': self.option('install-method'),
            'brew_tasks': [],
            'brew_builds': [],
            'brew_server': self.shared('primary_task').ARTIFACT_NAMESPACE,
            'rpm_blacklist': self.option('install-rpms-blacklist')
        }

        if self.option('install-task-not-build'):
            self.debug('asked to install by task ID')

            options['brew_tasks'] = [task.task_id for task in tasks]

        else:
            for task in tasks:
                if task.scratch:
                    self.debug('task {} is a scratch build, using task ID for installation')

                    options['brew_tasks'].append(task.task_id)

                else:
                    self.debug('task {} is a regular task, using build ID for installation')

                    options['brew_builds'].append(task.build_id)

        options['brew_tasks'] = ' '.join(str(i) for i in options['brew_tasks'])
        options['brew_builds'] = ' '.join(str(i) for i in options['brew_builds'])

        job_xml = """
            <job>
              <recipeSet priority="Normal">
                <recipe ks_meta="method=http harness='restraint-rhts beakerlib-redhat'" whiteboard="Server">
                  <task name="/distribution/install/brew-build" role="None">
                    <params>
                      <param name="BASEOS_CI" value="true"/>
                      <param name="METHOD" value="{brew_method}"/>
                      <param name="TASKS" value="{brew_tasks}"/>
                      <param name="BUILDS" value="{brew_builds}"/>
                      <param name="SERVER" value="{brew_server}"/>
                      <param name="RPM_BLACKLIST" value="{rpm_blacklist}"/>
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

        sut_install_logs = None

        match = re.search(r'Using (\./tmp[a-zA-Z0-9\._]+?) for job run', output.stdout)
        if match is not None:
            sut_install_logs = '{}/index.html'.format(match.group(1))

            if 'BUILD_URL' in os.environ:
                sut_install_logs = utils.treat_url('{}/artifact/{}'.format(os.getenv('BUILD_URL'), sut_install_logs),
                                                   logger=self.logger)

            self.info('SUT installation logs are in {}'.format(sut_install_logs))

        if output.exit_code != 0:
            self.debug('restraint exited with invalid exit code {}'.format(output.exit_code))

            raise SUTInstallationFailedError(self.shared('primary_task'),
                                             '<Not available>' if sut_install_logs is None else sut_install_logs)

    def create_schedule(self, tasks, job_desc, image):
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
                                        self._setup_guest, fn_args=(tasks, guest,),
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
        self.require_shared('restraint', 'tasks', 'image')

        tasks = self.shared('tasks')

        image = self.shared('image')
        if image is None:
            raise GlueError('No image found.')

        def _command_options(name):
            opts = self.option(name)
            if opts is None or not opts:
                return []

            return shlex.split(opts)

        # workflow-tomorrow
        jobs = self._run_wow()

        if len(jobs) > 1:
            raise GlueError('Multiple planned wow jobs are not supported')

        job = jobs[0]

        self.debug('job as planned by wow:\n{}'.format(job.prettify(encoding='utf-8')))

        self.create_schedule(tasks, job, image)
