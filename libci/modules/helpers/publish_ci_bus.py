import stomp
from libci import CIError, Module, utils

# defaults
CI_BUS_HOST = 'ci-bus.lab.eng.rdu2.redhat.com'
CI_BUS_PORT = 61613
CI_BUS_TOPIC = '/topic/CI'


class CIPublishCiBus(Module):
    """
    This module sends notifications of CI results via CI message bus.

    Inspiration of this code comes from:
    http://git.app.eng.bos.redhat.com/git/ci-ops-tools.git/tree/jenkins/plugins/redhat-ci-plugin/scripts/publish.py
    """

    name = 'publish-ci-bus'
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

    def publish(self, message):
        headers = message[0]
        # body needs to be a string
        body = utils.format_dict(message[1])
        test_type = message[2]

        utils.log_blob(self.debug,
                       'sent following message to CI message bus',
                       'header:\n{}\nbody:\n{}'.format(utils.format_dict(headers), body))

        self.info('{} message published to CI message bus'.format(test_type))

        if self.option('dry-run'):
            return

        if stomp.__version__[0] < 4:
            # pylint: disable=no-value-for-parameter
            self.cibus.send(message=body, headers=headers, destination=self.option('destination'))
        else:
            self.cibus.send(body=body, headers=headers, destination=self.option('destination'))

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
        messages = self.shared('bus_messages') or []
        if self.option('dry-run'):
            self.info('running in dry-run mode, no messages will be sent out')

        for message in messages:
            self.publish(message)
