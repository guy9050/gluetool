import os
import libci


#: Default log path, leads to Jenkins' userContent directory
DEFAULT_LOG_FILE = 'simple-wow-export.log'


#: Default record template
DEFAULT_TEMPLATE = '{BREW_TASK_ID};{NAME};{VERSION};{RELEASE};{SCRATCH};{RESULT};{JENKINS_JOB_URL};{BEAKER_MATRIX_URL}'


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
            'help': 'Name of the log file. Default is {}'.format(
                DEFAULT_LOG_FILE),
            'default': DEFAULT_LOG_FILE
        },
        'template': {
            'help': 'Record template. See module source for all available fields.',
            'default': DEFAULT_TEMPLATE
        }
    }

    @libci.utils.cached_property
    def template(self):
        tmpl = self.option('template')
        self.info("Template: '{}'".format(tmpl))

        return tmpl

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
            'NAME': task.component,
            'VERSION': task.version,
            'RELEASE': task.release,
            'SCRATCH': 'scratch' if task.scratch is True else '',
            'RESULT': result['result'],
            'JENKINS_JOB_URL': result['urls'].get('jenkins_job', ''),
            'BEAKER_MATRIX_URL': result['urls'].get('beaker_matrix', '')
        }

        return self.template.format(**variables)

    def _flush_records(self, records):
        """
        Log results into a log file.
        """

        with open(self.option('log'), 'w') as f:
            f.write('\n'.join(records) + '\n')
            f.flush()

    def sanity(self):
        # check whether the template can be used as Python templating string

        variables = ('BREW_TASK_ID', 'BREW_TASK_ISSUER', 'BREW_TASK_TARGET', 'NVR', 'SCRATCH', 'RESULT',
                     'JENKINS_JOB_URL', 'BEAKER_MATRIX_URL', 'NAME', 'VERSION', 'RELEASE')

        try:
            self.template.format(**{v: '' for v in variables})

        except KeyError as exc:
            raise libci.CIError("Template contains unknown key '{}'".format(exc.args[0]))

    def execute(self):
        task = self.shared('brew_task')
        if not task:
            raise libci.CIError('Unable to get brew task')

        results = self.shared('results') or []
        records = []

        for result in results:
            self.debug('result:\n{}'.format(libci.utils.format_dict(result)))

            if result['type'] != 'wow':
                continue

            records.append(self._format_record(task, result))

        if records:
            self.info('Logging wow results into a log file {}'.format(self.option('log')))
            self._flush_records(records)

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

        self._flush_records([self._format_record(task, fake_result)])
