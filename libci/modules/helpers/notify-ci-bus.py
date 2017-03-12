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
        if stomp.__version__[0] < 4:
            # pylint: disable=no-value-for-parameter
            self.cibus.send(message=body, headers=headers, destination=self.option('destination'))
        else:
            self.cibus.send(body=body, headers=headers, destination=self.option('destination'))

    def publish_rpmdiff(self, result):
        for subresult in result['rpmdiff']:
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

    def publish_result(self, result):
        publish_function = getattr(self, 'publish_{}'.format(result['type']), None)
        if publish_function:
            publish_function(result)
        else:
            self.warn("skipping unsupported result type '{}'".format(result['type']))

    def sanity(self):
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

        for result in results:
            self.publish_result(result)
