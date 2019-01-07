import collections
import os
import sys
import urlparse
import imp

import bs4

import gluetool
from gluetool import GlueError, SoftGlueError, GlueCommandError, utils
from gluetool.log import BlobLogger
from gluetool.utils import load_yaml, run_command, fetch_url
from libci.results import TestResult, publish_result
from libci.sentry import PrimaryTaskFingerprintsMixin


REQUIRED_COMMANDS = ['bkr', 'beaker-jobwatch', 'tcms-results']

TCMS_RESULTS_LOCATIONS = ('/bin', '/usr/bin')

DEFAULT_RESERVE_TIME = 24


class SUTInstallationFailedError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task, installation_logs):
        super(SUTInstallationFailedError, self).__init__(task, 'SUT installation failed')

        self.installation_logs = installation_logs


class BeakerError(PrimaryTaskFingerprintsMixin, GlueError):
    """
    Hard exception, used instead plain :py:class:`gluetool.GlueError` when task context
    is available and reasonable.
    """


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

    The option ``--critical-tasks-list`` expects a yaml file with list of tasks which throw a SUT installation soft
    error. An example is shown below:

    .. code-block:: shell

      $ cat critical_tasks.yaml
      ---
      - /distribution/setup
      - /distribution/install/brew-build
    """

    name = 'beaker'
    description = 'Runs tests on Beaker boxes.'

    options = {
        'job': {
            'help': 'Instead of creating a new run, inspect the existing job ID.',
            'metavar': 'ID',
            'type': int
        },
        'reserve': {
            'help': 'Do not release machines back to Beaker, keep them reserved',
            'action': 'store_true'
        },
        'reserve-time': {
            'help': 'Reservation time in hours (default: %(default)s)',
            'default': DEFAULT_RESERVE_TIME,
            'metavar': 'HOURS',
            'type': int
        },
        'critical-tasks-list': {
            'help': """
                    Yaml file with tasks which are critical for testing. These usually prepare the SUT
                    for testing, do not performd actual testing and their failure is considered
                    as and SUT installation error. Failures in these tasks will cause an soft error.

                    See the module help for an example yaml file for this option.
                    """
        }
    }

    _processed_results = None

    @gluetool.utils.cached_property
    def critical_tasks(self):
        if not self.option('critical-tasks-list'):
            return []

        return load_yaml(self.option('critical-tasks-list'))

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
        processed_results = self._processed_results = collections.OrderedDict()

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

        return [(bs4.BeautifulSoup(output.stdout, 'xml'), [job_id])]

    def _run_wow(self):
        # pylint: disable=too-many-statements
        """
        Create Beaker jobs XMLs for allowed distros.

        :rtype: list(tuple(element, list(int)))
        :returns: List of pairs ``(job XML, [Beaker job ID #1, Beaker job ID #2, ...])``
        """

        if self.option('job'):
            return self._reuse_job(self.option('job'))

        options = []

        # add options to install the task
        options += self.shared('wow_artifact_installation_options')

        # We could use --reserve but we must be sure the reservesys is *the last* task in the recipe
        # - users may require their own "last" tasks and --last-task is mightier than mere --reserve.
        # We're also adding a "padding" task: beaker-jobwatch can handle just a single end-task, and
        # obviously it cannot be /distribution/reservesys (jobwatch would wait till the end of reservation).
        # The last task before the reservesys must be the same, so we could tell jobwatch to quit
        # when it reaches this task in all recipes.
        if self.option('reserve'):
            options += [
                '--last-task="/distribution/utils/dummy"',
                '--last-task="RESERVETIME={}h /distribution/reservesys"'.format(self.option('reserve-time'))
            ]
        else:
            options += ['--no-reserve']

        return [
            (job, self.shared('submit_beaker_jobs', [job])) for job in self.shared('beaker_job_xml', options=options)
        ]

    def _run_jobwatch(self, jobs):
        """
        Start beaker-jobwatch, to baby-sit our jobs, and wait for its completion.

        :param list(tuple(element, list(int))) jobs: List of pairs ``(job XML, [Beaker job ID #1,
            Beaker job ID #2, ...])``
        :rtype: tuple(gluetool.utils.ProcessOutput, str)
        :returns: output of ``beaker_jobwatch`` shared function: ``beaker-jobwatch`` output
            (:py:class:`gluetool.utils.ProcessOutput` instance) and the final Beaker matrix URL.
        """

        return self.shared('beaker_jobwatch',
                           sum([job_ids for _, job_ids in jobs], []),
                           end_task='/distribution/utils/dummy' if self.option('reserve') else None,
                           critical_tasks=self.critical_tasks)

    def _process_jobs(self, matrix_url):
        """
        Tries to parse beaker-jobwatch output, and looks for list of beaker
        jobs. It then inspects these jobs, using tcms-results, to gather
        a summary for other interested parties.

        :param str matrix_url: Beaker matrix URL, used to extract all relevant Beaker jobs.
        :returns: tuple of three items: string result, dict with processed results, beaker matrix URL
        """

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

                # in case a SUT task failed, report it as ERROR
                if task in self.critical_tasks:
                    self.debug('            We have found a failed critical task!')
                    return 'ERROR', self._processed_results

                self.debug('            We have our traitor!')
                return 'FAIL', self._processed_results

        return 'PASS', self._processed_results

    def execute(self):
        self.require_shared('wow_artifact_installation_options', 'tasks', 'primary_task', 'beaker_job_xml',
                            'parse_beah_result', 'beaker_jobwatch', 'submit_beaker_jobs')

        # workflow-tomorrow
        jobs = self._run_wow()

        # Log the initial beaker matrix - it will be logged by beaker-jobwatch later,
        # but beaker-jobwatch will be running behind a pipe and one or two buffers,
        # and no amount of flushes can force the pipe to present it sooner then after
        # a period of waiting for another refresh of jobs it's managing. But it's
        # useful information.
        # Pick all job IDs returned by wow invocation, merge them into a single list,
        # and insert them into a URL.

        # pylint: disable=line-too-long
        self.info('Initial Beaker matrix: https://beaker.engineering.redhat.com/matrix/?toggle_nacks_on=on&job_ids={}'.format(
            '+'.join([str(job_id) for job_id in sum([job_ids for _, job_ids in jobs], [])])
        ))

        # beaker-jobwatch
        _, matrix_url = self._run_jobwatch(jobs)

        # evaluate jobs
        overall_result, processed_results = self._process_jobs(matrix_url)

        self.info('Result of testing: {}'.format(overall_result))

        publish_result(self, BeakerTestResult, overall_result, matrix_url, payload=processed_results)

        # for an SUT error we need to report a soft error
        if overall_result == 'ERROR':
            raise SUTInstallationFailedError(self.shared('primary_task'), matrix_url)
