import stomp
from libci import CIError, Module, utils


class CIPublisherCiBus(Module):
    """
    This module sends notifications of CI results via CI message bus.
    """

    name = 'publisher-ci-bus'
    description = 'Notification module - CI msg bus'
    options = {
        'destination': {
            'help': 'Message bus topic/subscription',
        },
        'dry-run': {
            'help': 'Do not send notifications.',
            'action': 'store_true',
        },
        'host': {
            'help': 'Message bus host'
        },
        'password': {
            'help': 'Password used for authentication',
        },
        'port': {
            'help': 'Message bus port',
        },
        'user': {
            'help': 'User used for authentication',
        },
    }
    required_options = ['user', 'password', 'destination', 'host', 'port']

    def publish(self, message):
        # body needs to be a string
        body = utils.format_dict(message.body)

        utils.log_blob(self.debug,
                       'sent following message to CI message bus',
                       'header:\n{}\nbody:\n{}'.format(utils.format_dict(message.headers), body))

        if self.option('dry-run'):
            return

        if stomp.__version__[0] < 4:
            # pylint: disable=no-value-for-parameter
            self.cibus.send(message=body, headers=message.headers, destination=self.option('destination'))
        else:
            self.cibus.send(body=body, headers=message.headers, destination=self.option('destination'))

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
        messages = self.shared('bus_messages') or {}

        if not messages:
            self.warn('No messages to send, did you call make-bus-messages module before this one?')

        if self.option('dry-run'):
            self.info('running in dry-run mode, no messages will be sent out')

        for message_type in messages.keys():
            messages_of_one_type = messages[message_type]

            for message in messages_of_one_type:
                self.publish(message)

            count = len(messages_of_one_type)
            plural = "message" if count == 1 else "messages"

            self.info('{0} {1} {2} published to CI message bus'.format(count, message_type, plural))
