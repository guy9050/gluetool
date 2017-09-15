import os

import libci
from libci import Module

import proton
import proton.handlers
import proton.reactor


class ContainerAdapter(libci.log.ContextAdapter):
    def __init__(self, logger, handler):
        super(ContainerAdapter, self).__init__(logger, {'ctx_container_url': (100, handler.url)})


class TestHandler(proton.handlers.MessagingHandler):
    def __init__(self, module, url, messages, topic, *args, **kwargs):
        super(TestHandler, self).__init__(*args, **kwargs)

        self._module = module

        self.url = url
        self.messages = messages
        self.topic = topic
        self.pending = {}

        self.logger = ContainerAdapter(module.logger, self)
        self.logger.connect(self)

        self._step_timeout = None
        self._global_timeout = None

    def _set_timeout(self, container, name, delay, label):
        attr = '_{}_timeout'.format(name)

        self._cancel_timeout(name)

        self.debug('  setting {} timeout to {} seconds: {}'.format(name, delay, label))

        setattr(self, attr, container.schedule(delay, self))

    def _cancel_timeout(self, name):
        attr = '_{}_timeout'.format(name)

        task = getattr(self, attr)

        if task is None:
            return

        self.debug('  canceling {} timeout'.format(name))

        task.cancel()
        setattr(self, attr, None)

    def _stop(self, event):
        self.debug('  stopping container: {}'.format(event))

        self._cancel_timeout('step')
        self._cancel_timeout('global')

        if event.container:
            event.container.stop()

        if event.connection:
            event.connection.close()

    def on_start(self, event):
        self.debug('on_start: {}'.format(event))

        event.container.connected = False
        ssl = proton.SSLDomain(proton.SSLDomain.MODE_CLIENT)

        # pylint: disable=protected-access
        certificate = os.path.expanduser(self._module._environment['certificate'])
        broker_ca = os.path.expanduser(self._module._environment['broker-ca'])

        ssl.set_credentials(certificate, certificate, None)
        ssl.set_trusted_ca_db(broker_ca)
        ssl.set_peer_authentication(proton.SSLDomain.VERIFY_PEER)
        conn = event.container.connect(url=self.url, reconnect=False, ssl_domain=ssl)

        event.container.create_sender(conn, target=self.topic)

        self._set_timeout(event.container, 'step', 5, 'waiting for connection')
        self._set_timeout(event.container, 'global', 30, 'global timeout')

    def on_timer_task(self, event):
        self.debug('on_timer_task: {}'.format(event))

        self.warn('timeout expired, stopping container')

        self._stop(event)

    def on_connection_opened(self, event):
        self.debug('on_connection_opened: {}'.format(event))

        event.container.connected = True

        self.debug('  connection opened successfully: {}'.format(event.connection.hostname))

        self._set_timeout(event.container, 'step', 30, 'waiting for sendable')

    def on_sendable(self, event):
        self.debug('on_sendable: {}'.format(event))

        self._cancel_timeout('step')

        self.send_messages(event)

    def send_messages(self, event):
        self.debug('send_messages: {}'.format(event))

        for message in self.messages:
            self.debug('  sending the message')
            libci.log.log_dict(self.debug, '  header', message.headers)
            libci.log.log_dict(self.debug, '  body', message.body)

            if not self._module.dryrun_allows('Sending messages to the message bus'):
                del self.messages[message]
                continue

            pending_message = proton.Message(address=self.topic, body=libci.log.format_dict(message.body),
                                             content_type='text/json')
            self.debug('  pending message: {}'.format(pending_message))

            delivery = event.sender.send(pending_message)
            self.pending[delivery] = message

        event.sender.close()

    def update_pending(self, event):
        self.debug('update_pending: {}'.format(event))

        del self.pending[event.delivery]

        if self.pending:
            return

        self.debug('  no more pending messages')

        if self.messages:
            self.debug('  {} messages unsent (rejected or released)'.format(len(self.messages)))

        else:
            self.debug('  all messages successfully sent')

        self._stop(event)

    def on_settled(self, event):
        self.debug('on_settled: {}'.format(event))

        msg = self.pending[event.delivery]
        self.messages.remove(msg)

        self.update_pending(event)

    def on_rejected(self, event):
        self.debug('on_rejected: {}'.format(event))

        self.update_pending(event)

    def on_released(self, event):
        self.debug('on_released: {}'.format(event))

        self.update_pending(event)

    def on_link_error(self, event):
        self.debug('on_link_error: {}'.format(event))

        self.warn('link error: {}'.format(event.link.remote_condition.name))
        self.warn(event.link.remote_condition.description)

        self._stop(event)

    def on_transport_tail_closed(self, event):
        self.debug('on_transport_tail_closed: {}'.format(event))

        self._stop(event)


class UMBPublisher(Module):
    """
    This module sends messages via Unified Message Bus (UMB).
    """

    name = 'publisher-umb-bus'
    description = 'Sending messages over UMB.'

    options = {
        'environments': {
            'help': 'Definitions of UMB environments.',
            'metavar': 'FILE'
        },
        'environment': {
            'help': 'What environment to use.'
        }
    }

    required_options = ('environments', 'environment')
    shared_functions = ('publish_bus_messages',)

    supported_dryrun_level = libci.ci.DryRunLevels.ISOLATED

    def __init__(self, *args, **kwargs):
        super(UMBPublisher, self).__init__(*args, **kwargs)

        self._environment = None

    def publish_bus_messages(self, messages, topic=None, **kwargs):
        """
        Publish one or more message to the message bus.

        A message is an object with two properties:

            * ``headers`` - a ``dict`` representing `headers` of the message,
            * ``body`` - an objet representing the actual data being send over the bus. Its actual
              type depends entirely on the message, it can be ``dict`` or``list`` or any other primitive
              type.

        :param list messages: Either ``list`` or a single `message`.
        :param str topic: If set, overrides the bus topic set by the configuration.
        :raises libci.ci.CIError: When there are messages that module failed to send.
        """

        # preserve original arguments for later call of publish_bus_messages
        original_args = (messages,)
        original_kwargs = libci.utils.dict_update({
            'topic': topic
        }, kwargs)

        if not isinstance(messages, list):
            messages = [messages]

        # copy given list of messages - we want to pass the original down the pipeline later, therefore
        # we must not touch it.
        message_buffer = messages[:]

        topic = topic or self._environment.get('topic', None)

        messages_count = len(message_buffer)

        for url in self._environment['urls']:
            self.info("Creating a container for: '{}'".format(url))

            container = proton.reactor.Container(TestHandler(self, url, message_buffer, topic))
            container.run()

            if container.connected:
                self.info('Container connected successfully')

            if not messages:
                self.info('{} messages successfully sent'.format(messages_count))
                break

            self.warn('Failed to sent out all messages, {} remaining'.format(len(message_buffer)))

        if message_buffer:
            raise libci.CIError('Could not send all the messages, {} remained.'.format(len(message_buffer)))

        self.shared('publish_bus_messages', *original_args, **original_kwargs)

    def execute(self):
        environments = libci.utils.load_yaml(self.option('environments'), logger=self.logger)

        self._environment = environments.get(self.option('environment'), None)

        if self._environment is None:
            raise libci.CIError("No such environment '{}'".format(self.option('environment')))
