import stomp

import libci
from libci import CIError, Module
from libci.ci import DryRunLevels
from libci.log import format_dict


class CIBusPublisher(Module):
    """
    This module sends messages via CI message bus.
    """

    name = 'publisher-ci-bus'
    # pylint: disable=line-too-long
    description = 'This module provides shared function to send messages via a messaging server (such as Apollo or RabbitMQ) using the STOMP protocol.'

    options = {
        'destination': {
            'help': 'Message bus topic/subscription.',
        },
        'host': {
            'help': 'Message bus host.'
        },
        'password': {
            'help': 'Password used for authentication',
        },
        'port': {
            'help': 'Message bus port.',
        },
        'user': {
            'help': 'User used for authentication.',
        }
    }

    required_options = ('user', 'password', 'destination', 'host', 'port')
    shared_functions = ('publish_bus_messages',)

    supported_dryrun_level = DryRunLevels.ISOLATED

    @libci.utils.cached_property
    def _session(self):
        # skip connecting if in isolated mode
        if not self.isolatedrun_allows('Connecting to the message bus'):
            return None

        session = stomp.Connection([(self.option('host'), self.option('port'))])
        session.start()

        try:
            session.connect(login=self.option('user'), passcode=self.option('password'), wait=True)

        except stomp.exception.ConnectFailedException:
            raise CIError('could not connect to CI message bus')

        if session.is_connected() is not True:
            raise CIError('could not connect to CI message bus')

        return session

    def publish_bus_messages(self, messages, **kwargs):
        # pylint: disable=unused-argument
        """
        Publish one or more message to the message bus.

        A message is an object with two properties:

            * ``headers`` - a ``dict`` representing `headers` of the message,
            * ``body`` - an object representing the actual data being send over the bus. Its actual
              type depends entirely on the message, it can be ``dict`` or``list`` or any other primitive
              type.

        :param list messages: Either ``list`` or a single `message`.
        :param str topic: If set, overrides the bus topic set by the configuration.
        :raises libci.ci.CIError: When there are messages that module failed to send.
        """

        if not isinstance(messages, list):
            messages = [messages]

        for message in messages:
            # body needs to be a string
            body = format_dict(message.body)

            self.debug('sending the message')
            libci.log.log_dict(self.debug, 'header', message.headers)
            libci.log.log_dict(self.debug, 'body', message.body)

            if not self.dryrun_allows('Sending messages to message bus'):
                continue

            self._session.send(body=body, headers=message.headers, destination=self.option('destination'))
