import os
import collections
from libci import Module
from libci.ci import DryRunLevels

Message = collections.namedtuple('Message', ('headers', 'body'))


class CIExporterResultsDB(Module):
    """
    This module converts results of testing to messages, which are then published on a message bus.
    """

    name = 'exporter-resultsdb'
    description = 'Export results to resultsDB with using publish method of publish* modules'

    options = {
        'topic-pattern': {
            'help': 'Pattern used to create message topics. It should contain variable named ``category``.',
            'type': str
        }
    }

    required_options = ('topic-pattern',)

    supported_dryrun_level = DryRunLevels.ISOLATED

    def process_rpmdiff_analysis(self, result, topic_spec='rpmdiff.analysis'):
        for subresult in result.payload:
            headers = {
                'CI_TYPE': 'resultsdb',
                'type': subresult['data']['type'],
                'testcase': subresult['testcase']['name'],
                'scratch': subresult['data']['scratch'],
                'taskid': subresult['data']['taskid'],
                'item': subresult['data']['item'],
            }

            topic = self.option('topic-pattern').format(category=topic_spec)
            self.shared('publish_bus_messages', Message(headers=headers, body=subresult), topic=topic)

    def process_rpmdiff_comparison(self, result):
        """
        Process rpmdiff comparison results. This function just calls rpmdiff analysis handler,
        as the processing is the same here. The process_* functions are named according to the
        test type and for rpmdiff we have two test types: rpmdiff-analysis and rpmdiff-comparison,
        thus the need to have a separate handler method.
        """
        self.process_rpmdiff_analysis(result, topic_spec='rpmdiff.comparison')

    def process_covscan(self, result):
        self.require_shared('primary_task')

        task = self.shared('primary_task')
        item = '{} {}'.format(task.nvr, result.baseline)

        headers = {
            'CI_TYPE': 'resultsdb',
            'item': item,
            'scratch': task.scratch,
            'taskid': task.task_id,
            'testcase': 'dist.covscan',
            'type': 'koji_build_pair'
        }

        body = {
            'data': {
                'item': item,
                'newnvr': task.nvr,
                'oldnvr': result.baseline,
                'scratch': task.scratch,
                'taskid': task.task_id,
                'type': 'koji_build_pair'
            },
            'outcome': result.overall_result,
            'ref_url': result.urls['covscan_url'],
            'testcase': {
                'name': 'dist.covscan',
                'ref_url': 'https://url.corp.redhat.com/covscan-in-ci'
            }
        }

        topic = self.option('topic-pattern').format(category='covscan')
        self.shared('publish_bus_messages', Message(headers=headers, body=body), topic=topic)

    def process_beaker(self, result):
        self.process_ci_metricsdata(result, 'beaker')

    def process_restraint(self, result):
        self.process_ci_metricsdata(result, 'restraint')

    def process_ci_metricsdata(self, result, result_type):
        """
        Process CI metricsdata, to be published. Note that this code will eventually be changed or replaced
        in favor of 'resultsdb' format. Currently it should be considered as a legacy format
        of CI messages used to report results from old BaseOS CI.
        """

        self.require_shared('primary_task', 'distro')

        task = self.shared('primary_task')
        distro = self.shared('distro')

        recipients = self.shared('notification_recipients', result_type=result_type)
        if recipients is None:
            recipients = ['unknown']

        # count the executed and failed tests from all results
        executed = 0
        failed = 0
        for name, runs in result.payload.iteritems():
            self.debug('consider task {}'.format(name))

            for run in runs:
                bkr_status, bkr_result = str(run['bkr_status']), str(run['bkr_result'])

                if bkr_status.lower() == 'completed':
                    executed += 1
                    if bkr_result.lower() == 'fail':
                        failed += 1

        results = [{
            'executor': 'CI_OSP' if result_type == 'restraint' else 'beaker',
            'executed': executed,
            'failed': failed
        }]

        headers = {
            'CI_TYPE': 'ci-metricsdata',
            'component': task.nvr,
            'taskid': task.task_id,
        }

        body = {
            'component': task.nvr,
            'trigger': 'brew build',
            'tests': results,
            'base_distro': distro,
            'brew_task_id': task.task_id,
            # fake job name for legacy reasons
            'job_name': 'ci-{}-brew-{}-2-runtest'.format(task.component, task.target),
            'build_type': os.environ.get('BUILD_TYPE', 'unknown'),
            'jenkins_job_url': os.environ.get('JOB_URL', 'unknown'),
            'jenkins_build_url': os.environ.get('BUILD_URL', 'unknown'),
            'build_number': os.environ.get('BUILD_NUMBER', 'unknown'),
            # hardcoded information currently
            'CI_tier': 1,
            'team': 'baseos',
            'recipients': ','.join(recipients)
        }

        topic = self.option('topic-pattern').format(category='tier1')
        self.shared('publish_bus_messages', Message(headers=headers, body=body), topic=topic)

    def process_result(self, result):
        # in case the results type is a multi word, replace '-' with '_' to get a valid function name
        process_function = getattr(self, 'process_{}'.format(result.test_type.replace('-', '_')), None)
        if process_function is not None:
            # we're sure process_function *is* callable
            # pylint: disable=not-callable
            process_function(result)
            self.info('{} results sent'.format(result.test_type))
        else:
            self.warn("skipping unsupported result type '{}'".format(result.test_type), sentry=True)

    def execute(self):
        results = self.shared('results') or []

        for result in results:
            self.process_result(result)
