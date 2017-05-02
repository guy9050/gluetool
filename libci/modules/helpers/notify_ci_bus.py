import os
import stomp
from libci import CIError, Module, utils

# defaults
CI_BUS_HOST = 'ci-bus.lab.eng.rdu2.redhat.com'
CI_BUS_PORT = 61613
CI_BUS_TOPIC = '/topic/CI'


class CINotifyBus(Module):
    """
    This module sends notifications of CI results via CI message bus.

    Inspiration of this code comes from:
    http://git.app.eng.bos.redhat.com/git/ci-ops-tools.git/tree/jenkins/plugins/redhat-ci-plugin/scripts/publish.py
    """

    name = 'notify-ci-bus'
    description = 'Notification module - CI msg bus'
    options = {
        'destination': {
            'help': 'Message bus topic/subscription (default: {})'.format(CI_BUS_TOPIC),
            'default': CI_BUS_TOPIC,
        },
        'dry-run': {
            'help': 'Do not send notifications.',
            'action': 'store_true',
        },
        'host': {
            'help': 'Message bus host (default: {})'.format(CI_BUS_HOST),
            'default': CI_BUS_HOST,
        },
        'password': {
            'help': 'Password used for authentication',
        },
        'port': {
            'help': 'Message bus port (default: {})'.format(CI_BUS_PORT),
            'default': CI_BUS_PORT,
        },
        'user': {
            'help': 'User used for authentication',
        },
    }
    required_options = ['user', 'password']

    def publish(self, headers, body):
        # body needs to be a string
        body = utils.format_dict(body)
        utils.log_blob(self.debug,
                       'sent following message to CI message bus',
                       'header:\n{}\nbody:\n{}'.format(utils.format_dict(headers), body))

        if self.option('dry-run'):
            return

        if stomp.__version__[0] < 4:
            # pylint: disable=no-value-for-parameter
            self.cibus.send(message=body, headers=headers, destination=self.option('destination'))
        else:
            self.cibus.send(body=body, headers=headers, destination=self.option('destination'))

    def publish_rpmdiff(self, result):
        for subresult in result.payload:
            headers = {
                'CI_TYPE': 'resultsdb',
                'type': subresult['data']['type'],
                'testcase': subresult['testcase']['name'],
                'scratch': subresult['data']['scratch'],
                'taskid': subresult['data']['taskid'],
                'item': subresult['data']['item'],
            }
            self.publish(headers, subresult)
        self.info('published RPMdiff results to CI message bus')

    def publish_covscan(self, result):
        task = self.shared('brew_task')
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

        self.publish(headers, body)
        self.info('published Covscan results to CI message bus')

    def publish_wow(self, result):
        self.publish_ci_metricsdata(result, 'wow')

    def publish_restraint(self, result):
        self.publish_ci_metricsdata(result, 'restraint')

    def publish_ci_metricsdata(self, result, result_type):
        """
        Publish CI metricsdata. Note that this code will eventually be changed or replaced
        in favor of 'resultsdb' format. Currently it should be considered as a legacy format
        of CI messages used to report results from old BaseOS CI.
        """
        task = self.shared('brew_task')
        if task is None:
            raise CIError('no brew task found in shared functions')
        distro = self.shared('distro')
        if distro is None:
            raise CIError('no distro found in shared functions')

        if task.scratch:
            self.warn('ignoring ci_metricsdata export of scratch build')
            return

        recipients = self.shared('notification_recipients', result_type=result_type)
        if recipients is None:
            recipients = 'unknown'

        # count the executed and failed tests from all results
        executed = 0
        failed = 0
        for name, runs in result.payload.iteritems():
            self.debug('consider task {}'.format(name))

            for run in runs:
                status, result = str(run['bkr_status']), str(run['bkr_result'])

                if status.lower() == 'completed':
                    executed += 1
                    if result.lower() == 'fail':
                        failed += 1

        results = {
            'executor': 'CI_OSP' if result_type == 'restraint' else 'beaker',
            'executed': executed,
            'failed': failed
        }

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
            'job_names': 'ci-{}-brew-{}-2-runtest'.format(task.component, task.target.target),
            'build_type': os.environ.get('BUILD_TYPE', 'unknown'),
            'jenkins_job_url': os.environ.get('JOB_URL', 'unknown'),
            'jenkins_build_url': os.environ.get('BUILD_URL', 'unknown'),
            'build_number': os.environ.get('BUILD_NUMBER', 'unknown'),
            # hardcoded information currently
            'CI_tier': '1',
            'team': 'baseos',
            'recipients': ','.join(recipients)
        }

        self.publish(headers, body)
        self.info('published CI Metrics data results to CI message bus')

    def publish_result(self, result):
        publish_function = getattr(self, 'publish_{}'.format(result.test_type), None)
        if publish_function is not None:
            # we're sure publish_function *is* callable
            # pylint: disable=not-callable
            publish_function(result)
        else:
            self.warn("skipping unsupported result type '{}'".format(result.test_type))

    def sanity(self):
        # skip connecting if in dry mode
        if self.option('dry-run'):
            return

        # connect to message bus
        self.cibus = stomp.Connection([(self.option('host'), self.option('port'))])
        self.cibus.start()
        try:
            self.cibus.connect(login=self.option('user'), passcode=self.option('password'), wait=True)
        except stomp.exception.ConnectFailedException:
            raise CIError('could not connect to CI message bus')
        if self.cibus.is_connected() is not True:
            raise CIError('could not connect to CI message bus')

    def execute(self):
        results = self.shared('results') or []
        if self.option('dry-run'):
            self.info('running in dry-run mode, no messages will be sent out')

        for result in results:
            self.publish_result(result)
