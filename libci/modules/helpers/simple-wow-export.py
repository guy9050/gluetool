import fcntl
import os
import libci


#: Default log path, leads to Jenkins' userContent directory
DEFAULT_LOG_FILE = '$JENKINS_HOME/userContent/simple-export-wow-results.log'


#: Default record template
DEFAULT_TEMPLATE = '{BREW_TASK_ID};{NVR};{SCRATCH};{RESULT};{JENKINS_JOB_URL};{BEAKER_MATRIX_URL}'


class SimpleWowExport(libci.Module):
    """
    Very simple export of workflow-tomorrow results for dashboard
    used by BaseOS Tools team. Nothing fancy, just appending basic
    data to a log file.
    """

    name = 'simple-wow-export'
    description = 'Very simple export of wow results into a log file.'

    options = {
        'log': {
            'help': 'Path to a log file. Module will expand env variables in the path. Default is {}'.format(
                DEFAULT_LOG_FILE),
            'default': DEFAULT_LOG_FILE
        },
        'template': {
            'help': 'Record template. See module source for all available fields.',
            'default': DEFAULT_TEMPLATE
        }
    }

    _log_filepath = None
    _template = None

    def _format_record(self, task, result):
        """
        Using a template and provided resources, create string representation
        of a result.

        :param dict task: brew task description, as provided by brew module.
        :param dict result: wow testing result, generated by wow module.
        """

        variables = {
            'BREW_TASK_ID': task.task_id,
            'BREW_TASK_ISSUER': task.owner,
            'BREW_TASK_TARGET': task.target,
            'NVR': task.nvr,
            'SCRATCH': 'scratch' if task.scratch is True else '',
            'RESULT': result['result'],
            'JENKINS_JOB_URL': result['urls'].get('jenkins_job', ''),
            'BEAKER_MATRIX_URL': result['urls'].get('beaker_matrix_url', '')
        }

        return self._template.format(**variables)

    def _log_result(self, task, result):
        """
        Log result into a file.

        This method must lock a log file to make sure there are no possible
        collisions between multiple ci-wow jobs (unlikely, right now Jenkins
        runs them one by one, not in parallel) or readers (quite likely).

        :param dict task: brew task description, as provided by brew module.
        :param dict result: wow testing result, generated by wow module.
        """

        if self._log_filepath is None:
            self._log_filepath = os.path.expandvars(self.option('log'))
            self.info("Log file: '{}'".format(self._log_filepath))

        if self._template is None:
            self._template = self.option('template')
            self.info("Template: '{}'".format(self._template))

        record = str(self._format_record(task, result))

        self.debug('logging result: {}'.format(record))

        with open(self._log_filepath, 'a') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)

            try:
                f.write(record + '\n')
                f.flush()

            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def execute(self):
        task = self.shared('brew_task')
        if not task:
            raise libci.CIError('Unable to get brew task')

        results = self.shared('results') or []

        for result in results:
            self.debug('result:\n{}'.format(libci.utils.format_dict(result)))

            if result['type'] != 'wow':
                continue

            self._log_result(task, result)

    def destroy(self, failure=None):
        if failure is None:
            return

        task = self.shared('brew_task')
        if task is None:
            raise libci.CIError('Cannot log result without having Brew task')

        self.info('Logging wow results into a log file {}'.format(self.option('log')))

        fake_result = {
            'result': 'ERROR',
            'urls': {
                'jenkins_job_url': os.getenv('BUILD_URL', '')
            }
        }

        self._log_result(task, fake_result)