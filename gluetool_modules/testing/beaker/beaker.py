import json
import os
import shlex
import sys
import urlparse
import imp

import bs4

import gluetool
from gluetool import GlueError, SoftGlueError, GlueCommandError, utils
from gluetool.log import BlobLogger
from gluetool.utils import run_command, fetch_url
from libci.results import TestResult, publish_result
from libci.sentry import PrimaryTaskFingerprintsMixin


REQUIRED_COMMANDS = ['bkr', 'beaker-jobwatch', 'tcms-results']

TCMS_RESULTS_LOCATIONS = ('/bin', '/usr/bin')

DEFAULT_RESERVE_TIME = 24


class BeakerError(PrimaryTaskFingerprintsMixin, GlueError):
    """
    Hard exception, used instead plain :py:class:`gluetool.GlueError` when task context
    is available and reasonable.
    """


class InvalidTasksError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task, test_tasks):
        super(InvalidTasksError, self).__init__(task, 'Invalid task names provided')

        self.tasks = test_tasks


class BeakerJobwatchError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task, matrix_url):
        super(BeakerJobwatchError, self).__init__(task, 'Beaker job(s) aborted, inform the user nicely')

        self.matrix_url = matrix_url


class BeakerTestResult(TestResult):
    # pylint: disable=too-few-public-methods

    def __init__(self, glue, overall_result, matrix_url, **kwargs):
        urls = kwargs.pop('urls', {})
        urls['beaker_matrix'] = matrix_url

        super(BeakerTestResult, self).__init__(glue, 'beaker', overall_result, urls=urls, **kwargs)

    @classmethod
    def _unserialize_from_json(cls, glue, input_data):
        return BeakerTestResult(glue, input_data['overall_result'], input_data['urls']['beaker_matrix'],
                                ids=input_data['ids'], urls=input_data['urls'], payload=input_data['payload'])

    def _serialize_to_xunit_property_dict(self, parent, properties, names):
        if 'beaker_matrix' in properties:
            gluetool.utils.new_xml_element('property', parent, name='baseosci.url.beaker-matrix',
                                           value=properties.pop('beaker_matrix'))

        super(BeakerTestResult, self)._serialize_to_xunit_property_dict(parent, properties, names)

    def _serialize_to_xunit(self):
        test_suite = super(BeakerTestResult, self)._serialize_to_xunit()

        if self.glue.has_shared('beah_xunit_serialize'):
            self.glue.shared('beah_xunit_serialize', test_suite, self)

        else:
            self.glue.warn("To serialize result to xUnit format, 'beah_xunit_serialize' shared function is required",
                           sentry=True)

        return test_suite


class Beaker(gluetool.Module):
    """
    This module runs test on Beaker boxes with beah harness.

    Needs some else to actualy provide the job XML (e.g. :py:mod:`gluetool_modules.testing.wow.WorkflowTomorrow`),
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
        'install-rpms-blacklist': {
            # pylint: disable=line-too-long
            'help': 'Regexp pattern (compatible with ``egrep``) - when installing build, matching packages will not be installed.',
            'type': str,
            'default': ''
        },
        'install-method': {
            'help': 'Yum method to use for installation (default: ``update``).',
            'type': str,
            'default': 'update'
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
            raise GlueError('Cannot import tcms-results')

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

                    except GlueCommandError:
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

        except GlueCommandError as exc:
            raise GlueError('Failed to re-create the job: {}'.format(exc.output.stderr))

        return [job_id], bs4.BeautifulSoup(output.stdout, 'xml')

    def _run_wow(self):
        # pylint: disable=too-many-statements
        """
        Create job XML and submit it to beaker.

        :returns: ([job #1 ID, job #2 ID, ...], <job />)
        """

        if self.option('job'):
            return self._reuse_job(self.option('job'))

        tasks = self.shared('tasks')
        primary_task = self.shared('primary_task')

        options = [
            '--first-testing-task', '/distribution/runtime_tests/verify-nvr-installed'
        ]

        if 'JOB_NAME' in os.environ and 'BUILD_ID' in os.environ:
            jenkins_build_id = '{} #{}'.format(os.getenv('JOB_NAME'), os.getenv('BUILD_ID'))
        else:
            jenkins_build_id = '<Unknown Jenkins build>'

        options += [
            '--whiteboard',
            'CI: {}: {}, brew task {}, build target {}'.format(jenkins_build_id, primary_task.nvr,
                                                               primary_task.task_id, primary_task.target)
        ]

        # gather builds and tasks
        brew_build_params = {
            'BUILDS': [],
            'TASKS': []
        }

        if self.option('install-task-not-build'):
            self.debug('asked to install by task ID')

            brew_build_params['TASKS'] = [task.task_id for task in tasks]

        else:
            for task in tasks:
                if task.scratch:
                    self.debug('task {} is a scratch build, using task ID for installation')

                    brew_build_params['TASKS'].append(task.task_id)

                else:
                    self.debug('task {} is a regular task, using build ID for installation')

                    brew_build_params['BUILDS'].append(task.build_id)

        # remove empty parameters
        if not brew_build_params['TASKS']:
            del brew_build_params['TASKS']

        if not brew_build_params['BUILDS']:
            del brew_build_params['BUILDS']

        # now squash param values into a space-separated strings: <param>: '1 2 3 4 ...'
        for param, values in brew_build_params.iteritems():
            brew_build_params[param] = ' '.join([str(i) for i in values])

        # add RPM blacklist - it's already a string...
        if self.option('install-rpms-blacklist'):
            brew_build_params['RPM_BLACKLIST'] = self.option('install-rpms-blacklist')

        # ... and a method
        brew_build_params['METHOD'] = self.option('install-method')

        # and convert params to a space-separated list of params, with values wrapped
        # by the quotes: <param>="foo bar" <param>="baz" ...
        brew_build_params = ' '.join(['{}="{}"'.format(param, value) for param, value in brew_build_params.iteritems()])

        options += [
            '--init-task',
            '{} /distribution/install/brew-build'.format(brew_build_params)
        ]

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

        except GlueCommandError as exc:
            if 'Invalid task(s):' in exc.output.stderr:
                s = exc.output.stderr.strip()
                tasks = [name.strip() for name in s[s.index('Invalid task(s)') + 17:-2].split(',')]

                raise InvalidTasksError(primary_task, tasks)

            raise BeakerError(primary_task, "Failure during 'job-submit' execution: {}".format(exc.output.stderr))

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
            raise BeakerError(primary_task, 'Cannot convert job-submit output to job ID: {}'.format(str(exc)))

        return (ids, job)

    def _run_jobwatch(self, jobs, job, options):
        """
        Start beaker-jobwatch, to baby-sit our jobs, and wait for its completion.

        :param list job: list of job IDs.
        :param element job: Job XML description.
        :param list options: additional options, usualy coming from jobwatch-options option.
        :returns: gluetool.utils.ProcessOutput with the output of beaker-jobwatch.
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

        except GlueCommandError as exc:
            # if beaker-jobwatch run unsuccessfuly, it exits with retcode 2
            # this most probably means that the jobs aborted
            if exc.output.exit_code == 2:
                matrix_url = self._get_matrix_url(exc.output.stdout)
                raise BeakerJobwatchError(self.shared('primary_task'), matrix_url)

            raise BeakerError(self.shared('primary_task'),
                              "Failure during 'jobwatch' execution: {}".format(exc.output.stderr))

        return output

    def _get_matrix_url(self, jobwatch_log):
        """
        Returns beaker matrix url parsed from beaker-jobwatch's output.

        :param str jobwatch_log: Output of beaker-jobwatch.
        :returns: matrix url as a string
        :raises: GlueError if output is invalid, matrix url not found or not finished
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
            raise BeakerError(self.shared('primary_task'), 'jobwatch output is unexpectedly short')

        if not jobwatch_log[-3].startswith('https://beaker.engineering.redhat.com/matrix/'):
            raise BeakerError(self.shared('primary_task'), 'Could not find beaker matrix URL in jobwatch output')

        self.info(jobwatch_log[-1].strip())
        if jobwatch_log[-1].strip() not in ['finished successfully', 'finished unsuccessfully']:
            raise BeakerError(self.shared('primary_task'), 'beaker-jobwatch does not report completion')

        self.info('beaker-jobwatch finished')

        # matrix url is always on the 3rd line from the end
        return jobwatch_log[-3].strip()

    def _process_jobs(self, jobwatch_log):
        """
        Tries to parse beaker-jobwatch output, and looks for list of beaker
        jobs. It then inspects these jobs, using tcms-results, to gather
        a summary for other interested parties.

        :param str jobwatch_log: Output of beaker-jobwatch.
        :returns: tuple of three items: string result, dict with processed results, beaker matrix URL
        """

        matrix_url = self._get_matrix_url(jobwatch_log)

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
                raise BeakerError(self.shared('primary_task'), 'Failed to process beaker results: {}'.format(str(e)))

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
        self.require_shared('tasks', 'primary_task', 'beaker_job_xml', 'parse_beah_result')

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