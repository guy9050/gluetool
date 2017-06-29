import json
import os
import shlex
import sys
import urlparse
import imp

import bs4

from libci import CIError, SoftCIError, CICommandError, Module, utils
from libci.log import BlobLogger
from libci.utils import run_command, fetch_url
from libci.results import TestResult, publish_result


REQUIRED_COMMANDS = ['bkr', 'beaker-jobwatch', 'tcms-results']

TCMS_RESULTS_LOCATIONS = ('/bin', '/usr/bin')

DEFAULT_RESERVE_TIME = 24


class NoTestAvailableError(SoftCIError):
    MODULE = 'beaker'
    SUBJECT = 'No tests found for the component'
    BODY = """

CI could not find any suitable tests for the component. This can have many different causes, e.g.:

    * component's configuration is incomplete, it does not provide correct test plan with tests
      for the component, or
    * the test plan is provided but it's empty, or
    * the test plan is not empty but there are filters applied in the configuration, and the result
      is an empty set of tests.

Please, see the documentation on CI configuration and what is required to correctly enable CI for
a component [1], current configuration [2], and/or consult with component's QE how to resolve
this situation.

[1] https://wiki.test.redhat.com/BaseOs/Projects/CI/Doc/UserHOWTO#EnableCIforacomponent
[2] https://gitlab.cee.redhat.com/baseos-qe/citool-config/raw/production/brew-dispatcher.yaml
    """

    def __init__(self):
        super(NoTestAvailableError, self).__init__('No tests provided for the component')


class InvalidTasksError(SoftCIError):
    MODULE = 'beaker'
    SUBJECT = 'Invalid tasks provided'
    BODY = """

One or more tasks provided to CI could not be found:

{tasks}

Please, see the documentation on CI configuration and what is required to correctly enable CI for
a component [1], current configuration [2], and/or consult with component's QE how to resolve
this situation.

[1] https://wiki.test.redhat.com/BaseOs/Projects/CI/Doc/UserHOWTO#EnableCIforacomponent
[2] https://gitlab.cee.redhat.com/baseos-qe/citool-config/raw/production/brew-dispatcher.yaml
    """

    def __init__(self, tasks):
        super(InvalidTasksError, self).__init__('Invalid task names provided')

        self.tasks = tasks

    def _template_variables(self):
        variables = super(InvalidTasksError, self)._template_variables()

        variables.update({
            'tasks': '\n'.join(['\t* {}'.format(name) for name in self.tasks])
        })

        return variables


class BeakerTestResult(TestResult):
    # pylint: disable=too-few-public-methods

    def __init__(self, overall_result, matrix_url, **kwargs):
        urls = {
            'beaker_matrix': matrix_url
        }

        super(BeakerTestResult, self).__init__('beaker', overall_result, urls=urls, **kwargs)


class Beaker(Module):
    """
    This module runs test on Beaker boxes with beah harness.

    Needs some else to actualy provide the job XML (e.g. :py:mod:`libci.modules.testing.wow.WorkflowTomorrow`),
    then submits this XML to the Beaker, babysits it with ``beaker-jobwatch``, and finally gets a summary
    using ``tcms-results``.
    """

    name = 'beaker'
    description = 'Runs tests on Beaker boxes.'

    options = {
        'jobwatch-options': {
            'help': 'Additional options for beaker-jobwatch'
        },
        'job': {
            'help': 'Instead of creating a new run, inspect the existing job ID.',
            'metavar': 'ID',
            'type': int
        },
        'install-task-not-build': {
            'help': 'Try to install SUT using brew task ID as a referrence, instead of the brew build ID.',
            'action': 'store_true',
            'default': False
        },
        'reserve': {
            'help': 'Do not release machines back to Beaker, keep them reserved',
            'action': 'store_true'
        },
        'reserve-time': {
            'help': 'Reservation time in hours (default: {})'.format(DEFAULT_RESERVE_TIME),
            'default': DEFAULT_RESERVE_TIME,
            'metavar': 'HOURS',
            'type': int
        }
    }

    _processed_results = None

    def sanity(self):
        # pylint: disable=too-many-statements

        utils.check_for_commands(REQUIRED_COMMANDS)

        for path in TCMS_RESULTS_LOCATIONS:
            try:
                self.tcms_results = tcms_results = imp.load_source('tcms_results', os.path.join(path, 'tcms-results'))
                break

            except ImportError as exc:
                self.warn('Cannot import tcms-results from {}: {}'.format(path, str(exc)))

        else:
            raise CIError('Cannot import tcms-results')

        # These are acessed by TaskAggregator.recordResult, and processed results must be
        # also accesible to other methods of this module.
        citool_module = self
        processed_results = self._processed_results = {}

        class TaskAggregator(tcms_results.TaskAggregator):
            # pylint: disable=too-few-public-methods

            """
            This class makes use of tcms-result's unique gift of parsing and processing
            of beaker data, and simply observes and stores processed data, to let us
            simply store the gathered data and present them to the world.
            """

            # pylint: disable=invalid-name
            def recordResult(self, task, caserun):
                """
                Overrides original method that does the actual processing of results.
                Our code lets it do its job, and when its done, we just store some
                of that data into our storage.
                """

                # call the original method to fulfill tcms-results duty
                super(TaskAggregator, self).recordResult(task, caserun)

                # possible exceptions are captured by tcms-results' code, and
                # all we get is just the message - not good enough.
                try:
                    if task.status != 'Completed':
                        citool_module.warn('Task {0} not completed'.format(task.name))
                        return

                    # This is XML provided by TCMS code - it lacks few interesting elements :/ But! Maybe we can do
                    # somethign about that...
                    task_xml = task.xml
                    task_id = int(task_xml.attributes['id'].value)

                    # Ask Beaker! It can return nice XML, with all those <logs> tags we like so much.
                    try:
                        output = run_command(['bkr', 'job-results', 'T:{}'.format(task_id)])

                        task_xml = bs4.BeautifulSoup(output.stdout, 'xml')

                    except CICommandError:
                        citool_module.warn('Cannot download result XML task {}. See log for details.'.format(task_id),
                                           sentry=True)

                        # Well, Beaker doesn't like use, then just re-wrap provided XML to BautifulSoup - it's
                        # one of Python's own XML DOMs, and we should stick with a single XML DOM implementation.
                        task_xml = bs4.BeautifulSoup(task_xml.toprettyxml())

                    journal_log = task_xml.find('log', attrs={'name': 'journal.xml'})
                    if journal_log:
                        _, journal_data = fetch_url(journal_log['href'], logger=citool_module.logger)
                        journal = bs4.BeautifulSoup(journal_data, 'xml').BEAKER_TEST

                    else:
                        journal = None

                    if task.name not in processed_results:
                        processed_results[task.name] = []

                    recipe_xml = bs4.BeautifulSoup(task.recipe.xml.toprettyxml(), 'xml')

                    processed_results[task.name].append(
                        citool_module.shared('parse_beah_result',
                                             task_xml.task, journal=journal, recipe=recipe_xml.recipe)
                    )

                except Exception as exc:
                    citool_module.exception('Exception raised while processing the task result: {}'.format(str(exc)))
                    raise

        # Replace the original TaskAggregator class with our custom version
        tcms_results.TaskAggregator = TaskAggregator

    def _reuse_job(self, job_id):
        # pylint: disable=no-self-use

        try:
            output = run_command(['bkr', 'job-clone', '--dryrun', '--xml', 'J:{}'.format(job_id)])

        except CICommandError as exc:
            raise CIError('Failed to re-create the job: {}'.format(exc.output.stderr))

        return [job_id], bs4.BeautifulSoup(output.stdout, 'xml')

    def _run_wow(self):
        """
        Create job XML and submit it to beaker.

        :returns: ([job #1 ID, job #2 ID, ...], <job />)
        """

        if self.option('job'):
            return self._reuse_job(self.option('job'))

        task = self.shared('task')

        options = []

        if task is not None:
            options += [
                '--first-testing-task', '/distribution/runtime_tests/verify-nvr-installed',
                '--whiteboard',
                'CI run {} brew task id {} build target {}'.format(task.nvr, task.task_id, task.target.target)
            ]

            if self.option('install-task-not-build'):
                self.debug('asked to install by task ID')

                options += ['--brew-task', str(task.task_id)]

            else:
                if task.scratch:
                    self.debug('task is a scratch build - using task ID for installation')

                    options += ['--brew-task', str(task.task_id)]

                else:
                    self.debug('task is a regular task - using build ID for installation')

                    options += ['--brew-build', str(task.build_id)]

        # we could use --reserve but we must be sure the reservesys is *the last* taskin the recipe
        # users may require their own "last" tasks and --last-task is mightier than mere --reserve.
        if self.option('reserve'):
            options += ['--last-task', 'RESERVETIME={}h /distribution/reservesys'.format(self.option('reserve-time'))]
        else:
            options += ['--no-reserve']

        output = self.shared('beaker_job_xml', options=options)

        job = bs4.BeautifulSoup(output.stdout, 'xml')

        with open('job.xml', 'w') as f:
            f.write(output.stdout)
            f.flush()

        # submit the job to beaker
        try:
            output = run_command(['bkr', 'job-submit', 'job.xml'])

        except CICommandError as exc:
            if 'Invalid task(s):' in exc.output.stderr:
                s = exc.output.stderr.strip()
                tasks = [name.strip() for name in s[s.index('Invalid task(s)') + 17:-2].split(',')]

                raise InvalidTasksError(tasks)

            raise CIError("Failure during 'job-submit' execution: {}".format(exc.output.stderr))

        try:
            # Submitted: ['J:1806666', 'J:1806667']
            jobs = output.stdout[output.stdout.index(' ') + 1:]
            # ['J:1806666', 'J:1806667']
            jobs = jobs.replace('\'', '"')
            # ["J:1806666", "J:1806667"]
            jobs = json.loads(jobs)
            # ['J:1806666', 'J:1806667']
            ids = [int(job_id.split(':')[1]) for job_id in jobs]
            # [1806666, 1806667]

        except Exception as exc:
            raise CIError('Cannot convert job-submit output to job ID: {}'.format(str(exc)))

        return (ids, job)

    def _run_jobwatch(self, jobs, job, options):
        """
        Start beaker-jobwatch, to baby-sit our jobs, and wait for its completion.

        :param list job: list of job IDs.
        :param element job: Job XML description.
        :param list options: additional options, usualy coming from jobwatch-options option.
        :returns: libci.utils.ProcessOutput with the output of beaker-jobwatch.
        """

        command = [
            'beaker-jobwatch',
            '--skip-broken-machines'
        ] + options + ['--job={}'.format(job_id) for job_id in jobs]

        if self.option('reserve'):
            next_to_last_tasks = {}

            for recipe_set in job.find_all('recipeSet'):
                for recipe in recipe_set.find_all('recipe'):
                    next_to_last_tasks[recipe.find_all('task')[-2]['name']] = True

            if len(next_to_last_tasks) > 1:
                self.warn('Multiple next-to-last tasks:\n{}'.format('\n'.join(next_to_last_tasks.keys())))
                self.warn('Multiple next-to-last tasks detected, beaker-jobwatch may not check them correctly',
                          sentry=True)

            command += [
                '--end-task={}'.format(task) for task in next_to_last_tasks.iterkeys()
            ]

        self.info("running 'beaker-jobwatch' to babysit the jobs")

        try:
            output = run_command(command, inspect=True)

        except CICommandError as exc:
            raise CIError("Failure during 'jobwatch' execution: {}".format(exc.output.stderr))

        return output

    def _process_jobs(self, jobwatch_log):
        """
        Tries to parse beaker-jobwatch output, and looks for list of beaker
        jobs. It then inspects these jobs, using tcms-results, to gather
        a summary for other interested parties.

        :param str jobwatch_log: Output of beaker-jobwatch.
        :returns: tuple of three items: string result, dict with processed results, beaker matrix URL
        """

        # beaker-jobwatch output usually looks like this:
        #
        # Broken: 0
        # Running:   0/1
        # Completed: 1/1
        # 	TJ#1739067
        # https://beaker.engineering.redhat.com/matrix/?toggle_nacks_on=on&job_ids=1739067
        # duration: 3:39:03.805050
        # finished successfully

        jobwatch_log = jobwatch_log.strip().split('\n')

        if len(jobwatch_log) < 3:
            raise CIError('jobwatch output is unexpectedly short')

        if not jobwatch_log[-3].startswith('https://beaker.engineering.redhat.com/matrix/'):
            raise CIError('Don\'t know where to find beaker matrix URL in jobwatch output')

        matrix_url = jobwatch_log[-3].strip()

        if jobwatch_log[-1].strip() != 'finished successfully':
            # When jobwatch failed, something serious probably happened. Give up
            # immediately.
            self.warn('beaker-jobwatch does not report successful completion')

            return 'ERROR', {}, matrix_url

        self.info('beaker-jobwatch finished successfully')

        parsed_matrix_url = urlparse.urlparse(matrix_url)
        parsed_query = urlparse.parse_qs(parsed_matrix_url.query)

        # tcms-results simply parses sys.argv... No other way to foist our options :/
        old_argv, sys.argv = sys.argv, ['/bin/tcms-results', '--job={}'.format(','.join(parsed_query['job_ids']))]
        try:
            self.tcms_results.options = self.tcms_results.parse()

        finally:
            sys.argv = old_argv

        # tcms-results just dumps its output to its (our...) sys.stdout/stderr, and does not allow for better
        # control all we can do is to flush stdout and stderr before doing any more logging from our code, to
        # avoid having tcms-results' output messing with our messages.
        def _flush_stdouts(*args, **kwargs):
            # pylint: disable=unused-argument

            sys.stdout.flush()
            sys.stderr.flush()

        # Now "run" tcms-results, to get a view on the results
        with BlobLogger('Output of tcms-results', outro='End of tcms-results output', on_finally=_flush_stdouts,
                        writer=self.info):
            try:
                self.tcms_results.Results().investigate().update()

            except Exception as e:
                self.error('Failed to process beaker results - the actual exception should have been logged already')
                raise CIError('Failed to process beaker results: {}'.format(str(e)))

        # Any single task with a result other than "PASS" - or even with
        # a status other than "COMPLETED" - means testing failed.
        #
        # Could be written in a more compact way, using generators and all/any
        # but this is more readable - and this part may change in the future,
        # depending on how we redefine "passed" result.

        self.debug('Try to find any non-PASS task')

        for task, runs in self._processed_results.iteritems():
            self.debug("    task '{}'".format(task))

            for run in runs:
                self.debug("        Status='{}', Result='{}'".format(run['bkr_status'], run['bkr_result']))

                if run['bkr_status'] == 'Completed' and run['bkr_result'] == 'Pass':
                    continue

                self.debug('            We have our traitor!')
                return 'FAIL', self._processed_results, matrix_url

        return 'PASS', self._processed_results, matrix_url

    def execute(self):
        def _command_options(name):
            opts = self.option(name)
            if opts is None or not opts:
                return []

            return shlex.split(opts)

        jobwatch_options = _command_options('jobwatch-options')

        # workflow-tomorrow
        job_ids, job = self._run_wow()

        # beaker-jobwatch
        jobwatch_output = self._run_jobwatch(job_ids, job, jobwatch_options)

        # evaluate jobs
        overall_result, processed_results, matrix_url = self._process_jobs(jobwatch_output.stdout)

        self.info('Result of testing: {}'.format(overall_result))

        publish_result(self, BeakerTestResult, overall_result, matrix_url, payload=processed_results)
