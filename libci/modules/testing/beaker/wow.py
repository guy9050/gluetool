import os
import shlex
import sys
import urlparse
import imp

from libci import CIError, CICommandError, Module, utils
from libci.utils import run_command, log_blob
from libci.results import TestResult, publish_result


REQUIRED_COMMANDS = ['bkr', 'beaker-jobwatch', 'tcms-results']

TCMS_RESULTS_LOCATIONS = ('/bin', '/usr/bin')


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
        }
    }

    _processed_results = None

    def sanity(self):
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

                if task.status != 'Completed':
                    self._wow_module.warn('Task {0} not completed'.format(task.name))
                    return

                DEBUG = citool_module.debug

                xml = task.xml
                journal = tcms_results.JournalParser(task)

                DEBUG("task: '{}'".format(task.name))
                log_blob(DEBUG, 'task XML', xml.toprettyxml())
                log_blob(DEBUG, 'task journal', journal.getDetails())

                machine = xml.getElementsByTagName('roles')[0].getElementsByTagName('system')[0]

                if task.name not in processed_results:
                    processed_results[task.name] = []

                data = {
                    'name': task.name,
                    'bkr_recipe_id': int(task.recipe.id),
                    'bkr_distrovariant': str(task.recipe.environment),
                    'bkr_task_id': int(xml.attributes['id'].value),
                    'bkr_version': xml.attributes['version'].value,
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
                duration = xml.attributes.get('duration')
                if duration is not None:
                    data['bkr_duration'] = 0

                    days_and_hours = duration.value.split(',')  # "1 day, 23:51:43"
                    if len(days_and_hours) > 1:
                        data['bkr_duration'] += int(days_and_hours.pop(0).split(' ')[0]) * 24 * 60 * 60

                    _chunks = [int(_chunk) for _chunk in days_and_hours[0].split(':')]
                    data['bkr_duration'] += _chunks[0] * 60 * 60 + _chunks[1] * 60 + _chunks[2]

                # store params, if any
                params_node = xml.getElementsByTagName('params')
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
                results_nodes = xml.getElementsByTagName('results')
                if results_nodes:
                    for result_node in results_nodes[0].getElementsByTagName('result'):
                        phase = result_node.attributes['path'].value
                        result = result_node.attributes['result'].value

                        data['bkr_phases'][phase] = result

                # store log links
                logs_nodes = xml.getElementsByTagName('logs')
                if logs_nodes:
                    for log_node in logs_nodes[0].getElementsByTagName('log'):
                        data['bkr_logs'].append({
                            'href': log_node.attributes['href'].value,
                            'name': log_node.attributes['name'].value
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
        :returns: libci.utils.ProcessOutput with the output of w-t.
        """

        distro_option = ['--distro={}'.format(distro)] if distro else []
        brew_option = ['--brew-task={}'.format(task.task_id)] if task is not None else []

        whiteboard = 'CI run {} brew task id {} build target {}'.format(task.nvr, task.task_id, task.target.target)

        # wow
        task_params = {
            'BASEOS_CI': 'true',
            'BASEOS_CI_COMPONENT': str(task.component)
        }

        command = [
            'bkr', 'workflow-tomorrow',
            '--id',
            '--whiteboard', whiteboard,
            '--no-reserve',
            '--decision'
        ] + distro_option + brew_option + options

        for name, value in task_params.iteritems():
            command += ['--taskparam', '{}={}'.format(name, value)]

        self.info("running 'workflow-tomorrow':\n{}".format(utils.format_command_line([command])))

        try:
            return run_command(command)

        except CICommandError as exc:
            # Check for most common causes, and raise soft error where necessary
            soft = False

            if 'No relevant tasks found in test plan' in exc.output.stderr:
                soft = True

            raise CIError("Failure during 'wow' execution: {}".format(exc.output.stderr), soft=soft)

    def _run_jobwatch(self, jobs, options):
        """
        Start beaker-jobwatch, to baby-sit our jobs, and wait for its completion.

        :param list job: list of job IDs.
        :param list options: additional options, usualy coming from jobwatch-options option.
        :returns: libci.utils.ProcessOutput with the output of beaker-jobwatch.
        """

        command = [
            'beaker-jobwatch',
            '--skip-broken-machines'
        ] + options + ['--job={}'.format(job) for job in jobs]

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
        wow_output = self._run_wow(task, distro, wow_options)

        # beaker-jobwatch
        jobwatch_output = self._run_jobwatch(wow_output.stdout.split(), jobwatch_options)

        # evaluate jobs
        overall_result, processed_results, matrix_url = self._process_jobs(jobwatch_output.stdout)

        self.info('Result of testing: {}'.format(overall_result))

        publish_result(self, WowTestResult, overall_result, matrix_url, payload=processed_results)
