import json
import os
import shlex
import sys
import urlparse
import imp

import bs4

from libci import CIError, SoftCIError, CICommandError, Module, utils
from libci.utils import run_command, log_blob, format_dict
from libci.results import TestResult, publish_result


REQUIRED_COMMANDS = ['bkr', 'beaker-jobwatch', 'tcms-results']

TCMS_RESULTS_LOCATIONS = ('/bin', '/usr/bin')

DEFAULT_RESERVE_TIME = 24


class NoTestAvailableError(SoftCIError):
    SUBJECT = 'No tests found for the component'
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

[1] https://wiki.test.redhat.com/BaseOs/Projects/CI/Documentation/UserHOWTO#AddthecomponenttoCI
[2] https://gitlab.cee.redhat.com/baseos-qe/citool-config/raw/production/brew-dispatcher.yaml
    """

    def __init__(self):
        super(NoTestAvailableError, self).__init__('No tests provided for the component')


class WowTestResult(TestResult):
    # pylint: disable=too-few-public-methods

    def __init__(self, overall_result, matrix_url, **kwargs):
        urls = {
            'beaker_matrix': matrix_url
        }

        super(WowTestResult, self).__init__('wow', overall_result, urls=urls, **kwargs)


class CIWow(Module):
    """
    This module wraps workflow-tomorrow, beaker-jobwatch and tcms-results, using them
    to create a simple testing pipeline for given build.

    w-t is used to kick of beaker jobs, using options passed by user. beaker-jobwatch
    than babysits jobs, and if everything goes well, tcms-results are used to gather
    results and create a simple summary.
    """

    name = 'wow'
    options = {
        'wow-options': {
            'help': 'Additional options for workflow-tomorrow'
        },
        'jobwatch-options': {
            'help': 'Additional options for beaker-jobwatch'
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

        if not self.option('wow-options'):
            raise NoTestAvailableError()

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

                if task.status != 'Completed':
                    citool_module.warn('Task {0} not completed'.format(task.name))
                    return

                DEBUG = citool_module.debug

                task_xml = task.xml
                recipe_xml = task.recipe.xml
                journal = tcms_results.JournalParser(task)

                DEBUG("task: '{}'".format(task.name))
                log_blob(DEBUG, 'task XML', task_xml.toprettyxml())
                DEBUG('task journal:\n{}'.format(format_dict(journal.getDetails())))

                machine = task_xml.getElementsByTagName('roles')[0].getElementsByTagName('system')[0]

                if task.name not in processed_results:
                    processed_results[task.name] = []

                data = {
                    'name': task.name,
                    'bkr_recipe_id': int(task.recipe.id),
                    'bkr_distrovariant': str(task.recipe.environment),
                    'bkr_task_id': int(task_xml.attributes['id'].value),
                    'bkr_version': task_xml.attributes['version'].value,
                    'bkr_arch': task.recipe.arch,
                    'bkr_status': task.status,
                    'bkr_result': task.result,
                    'bkr_host': machine.attributes['value'].value,
                    'connectable_host': machine.attributes['value'].value,
                    'bkr_duration': 0,
                    'bkr_params': [],
                    'bkr_packages': [],
                    'bkr_phases': {},
                    'bkr_logs': []
                }

                # convert test duration in a form of %h:%m:%s to simple integer (sec)
                # note: duration doesn't always need to be there, e.g. when "External
                # Watchdog Expired"
                duration = task_xml.attributes.get('duration')
                if duration is not None:
                    data['bkr_duration'] = 0

                    days_and_hours = duration.value.split(',')  # "1 day, 23:51:43"
                    if len(days_and_hours) > 1:
                        data['bkr_duration'] += int(days_and_hours.pop(0).split(' ')[0]) * 24 * 60 * 60

                    _chunks = [int(_chunk) for _chunk in days_and_hours[0].split(':')]
                    data['bkr_duration'] += _chunks[0] * 60 * 60 + _chunks[1] * 60 + _chunks[2]

                # store params, if any
                params_node = task_xml.getElementsByTagName('params')

                # not sure whether params_node would evaluate to False when it's empty
                # pylint: disable=len-as-condition
                if len(params_node) > 0:
                    for param in params_node[0].getElementsByTagName('param'):
                        # this is caserun.id, safe to ignore
                        if param.attributes['name'].value == 'CASERUNS':
                            continue

                        data['bkr_params'].append('%s="%s"' % (param.attributes['name'].value,
                                                               param.attributes['value'].value))

                # store packages, only unique names
                packages = {}
                for phase, phase_packages in journal.getDetails().iteritems():
                    map(packages.__setitem__, phase_packages, [])

                data['bkr_packages'] = [k.strip() for k in sorted(packages.keys())]

                # store phases
                results_nodes = task_xml.getElementsByTagName('results')
                if results_nodes:
                    for result_node in results_nodes[0].getElementsByTagName('result'):
                        phase = result_node.attributes['path'].value
                        result = result_node.attributes['result'].value

                        data['bkr_phases'][phase] = result

                # store log links - job results and journal do not have these, we have to ask
                # elsewhere...
                task_id = int(task_xml.attributes['id'].value)

                try:
                    output = run_command(['bkr', 'job-logs', 'T:{}'.format(task_id)])

                    for url in output.stdout.strip().splitlines():
                        url = url.strip()
                        name = url.split('/')[-1]

                        data['bkr_logs'].append({
                            'href': url,
                            'name': name
                        })

                except CICommandError:
                    msg = 'Cannot find logs for task {}. See log for details.'.format(task_id)

                    citool_module.warn(msg)
                    citool_module.ci.sentry_submit_warning(msg)

                if not data['bkr_logs']:
                    # construct at least TESTOUT.log
                    recipe_id = int(recipe_xml.attributes['id'].value)

                    # pylint: disable=line-too-long
                    url = 'https://beaker.engineering.redhat.com/recipes/{}/tasks/{}/logs/TESTOUT.log'.format(recipe_id, task_id)

                    data['bkr_logs'].append({
                        'href': url,
                        'name': 'TESTOUT.log'
                    })

                processed_results[task.name].append(data)

        # Replace the original TaskAggregator class with our custom version
        tcms_results.TaskAggregator = TaskAggregator

    def _run_wow(self, task, distro, options):
        """
        Run workflow-tomorrow to create beaker jobs, using options we
        got from the user.

        :param task: brew task info, as returned by `brew_task` shared function
        :param str distro: distribution to install.
        :param list options: additional options, usualy coming from wow-options option.
        :returns: ([job #1 ID, job #2 ID, ...], <job />)
        """

        distro_option = ['--distro={}'.format(distro)] if distro else []

        if task:
            install_option = [
                '--brew-task={}'.format(task.task_id)
            ]

            verify_option = [
                '--first-testing-task',
                '/distribution/runtime_tests/verify-nvr-installed'
            ]

        else:
            install_option = []
            verify_option = []

        whiteboard = 'CI run {} brew task id {} build target {}'.format(task.nvr, task.task_id, task.target.target)

        # wow
        task_params = {
            'BASEOS_CI': 'true',
            'BASEOS_CI_COMPONENT': str(task.component)
        }

        command = [
            'bkr', 'workflow-tomorrow',
            '--whiteboard', whiteboard,
            '--decision'
        ] + distro_option + install_option + verify_option + options

        for name, value in task_params.iteritems():
            command += ['--taskparam', '{}={}'.format(name, value)]

        # we could use --reserve but we must be sure the reservesys is *the last* taskin the recipe
        # users may require their own "last" tasks and --last-task is mightier than mere --reserve.
        if self.option('reserve'):
            command += ['--last-task', 'RESERVETIME={}h /distribution/reservesys'.format(self.option('reserve-time'))]
        else:
            command += ['--no-reserve']

        command += ['--dryrun']

        self.info("running 'workflow-tomorrow':\n{}".format(utils.format_command_line([command])))

        try:
            output = run_command(command)

        except CICommandError as exc:
            # Check for most common causes, and raise soft error where necessary
            if 'No relevant tasks found in test plan' in exc.output.stderr:
                raise NoTestAvailableError()

            if 'No recipe generated (no relevant tasks?)' in exc.output.stderr:
                raise NoTestAvailableError()

            raise CIError("Failure during 'wow' execution: {}".format(exc.output.stderr))

        job = bs4.BeautifulSoup(output.stdout, 'xml')

        with open('job.xml', 'w') as f:
            f.write(output.stdout)
            f.flush()

        # submit the job to beaker
        try:
            output = run_command(['bkr', 'job-submit', 'job.xml'])

        except CICommandError as exc:
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
                self.warn('Multiple next-to-last tasks detected, beaker-jobwatch may not check them correctly')
                self.warn('Next-to-last tasks:\n{}'.format('\n'.join(next_to_last_tasks.keys())))

                self.ci.sentry_submit_warning('[wow] Multiple next-to-last tasks detected')

            command += [
                '--end-task={}'.format(task) for task in next_to_last_tasks.iterkeys()
            ]

        self.info("running 'beaker-jobwatch':\n{}".format(utils.format_command_line([command])))

        command = ['bash', '-c', '{} | tee beaker-jobwatch.log'.format(' '.join(command))]

        try:
            output = run_command(command, stdout=utils.PARENT, stderr=utils.PARENT)

        except CICommandError as exc:
            raise CIError("Failure during 'jobwatch' execution: {}".format(exc.output.stderr))

        self.debug('output of beaker-jobwatch is stored in beaker-jobwatch.log')

        with open('beaker-jobwatch.log', 'r') as f:
            output.stdout = f.read().strip()

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

        jobwatch_log = jobwatch_log.split('\n')

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

        # Now "run" tcms-results, to get a view on the results
        self.info('tcms-results are working')

        try:
            self.tcms_results.Results().investigate().update()

        except Exception as e:
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
        task = self.shared('brew_task')
        if task is None:
            raise CIError('no brew build found, did you run brew module')

        distro = self.shared('distro')

        def _command_options(name):
            opts = self.option(name)
            if opts is None or not opts:
                return []

            return shlex.split(opts)

        wow_options = _command_options('wow-options')
        jobwatch_options = _command_options('jobwatch-options')

        # workflow-tomorrow
        job_ids, job = self._run_wow(task, distro, wow_options)

        # beaker-jobwatch
        jobwatch_output = self._run_jobwatch(job_ids, job, jobwatch_options)

        # evaluate jobs
        overall_result, processed_results, matrix_url = self._process_jobs(jobwatch_output.stdout)

        self.info('Result of testing: {}'.format(overall_result))

        publish_result(self, WowTestResult, overall_result, matrix_url, payload=processed_results)
