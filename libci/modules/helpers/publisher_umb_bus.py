import json
import re
import os
from libci import Module
from proton import Message, SSLDomain
from proton.reactor import Container
from proton.handlers import MessagingHandler


class TestHandler(MessagingHandler):
    def __init__(self, parent_module, url, messages, topic, *args, **kws):
        super(TestHandler, self).__init__(*args, **kws)
        self.parent_module = parent_module
        self.url = url
        self.messages = messages
        self.topic = topic
        self.pending = {}

        self.connect_task = None
        self.timeout_task = None

    def on_start(self, event):
        event.container.connected = False
        ssl = SSLDomain(SSLDomain.MODE_CLIENT)

        certificate = os.path.expanduser(self.parent_module.option('certificate'))
        cert_auth = self.parent_module.option('ca')

        ssl.set_credentials(certificate, certificate, None)
        ssl.set_trusted_ca_db(cert_auth)
        ssl.set_peer_authentication(SSLDomain.VERIFY_PEER)
        event.container.connect(url=self.url, reconnect=False, ssl_domain=ssl)

        self.connect_task = event.container.schedule(5, self)
        self.timeout_task = event.container.schedule(30, self)

    def on_timer_task(self, event):
        self.parent_module.verbose('on_timer_task: %s', event)
        if not event.container.connected:
            self.parent_module.info('not connected, stopping container')
            if self.timeout_task:
                self.timeout_task.cancel()
                self.timeout_task = None
            event.container.stop()
        else:
            # This should only run when called from the timeout task
            self.parent_module.info('timeout expired, stopping container')
            event.container.stop()

    def on_connection_opened(self, event):
        event.container.connected = True
        self.connect_task.cancel()
        self.connect_task = None
        self.parent_module.verbose('on_connection_opened %s', event)
        self.parent_module.verbose('Connection opened successfully: %s', event.connection.hostname)
        self.send_messages(event)

    def send_messages(self, event):
        sender = event.container.create_sender(event.connection, target=self.topic)

        for message in self.messages:
            pending_message = Message(properties=message.headers, body=message.body, address=self.topic)
            delivery = sender.send(pending_message)
            self.pending[delivery] = message
        sender.close()

    def update_pending(self, event):
        del self.pending[event.delivery]
        if not self.pending:
            self.parent_module.verbose('no pending messages')
            if self.messages:
                self.parent_module.verbose('%s messages unsent (rejected or released)', len(self.messages))
            else:
                self.parent_module.verbose('All messages successfully sent')
            if self.timeout_task:
                self.parent_module.verbose('canceling timeout task')
                self.timeout_task.cancel()
                self.timeout_task = None
            self.parent_module.verbose('closing connection %s', event.connection)
            event.connection.close()

    def on_settled(self, event):
        self.parent_module.verbose('on_settled %s', event)
        msg = self.pending[event.delivery]
        self.messages.remove(msg)
        self.update_pending(event)

    def on_rejected(self, event):
        self.parent_module.verbose('on_rejected', event)
        self.update_pending(event)

    def on_released(self, event):
        self.parent_module.verbose('on_released', event)
        self.update_pending(event)

    def on_link_error(self, event):
        self.parent_module.info('link error: %s', event.link.remote_condition.name)
        self.parent_module.info(event.link.remote_condition.description)
        self.parent_module.info('closing connection to: %s', event.connection.hostname)
        event.connection.close()

    def on_transport_tail_closed(self, event):
        self.parent_module.verbose('on_transport_tail_closed: %s', event)
        if self.connect_task:
            self.parent_module.verbose('canceling timer task')
            self.connect_task.cancel()
            self.connect_task = None
        if self.timeout_task:
            self.parent_module.verbose('canceling timeout task')
            self.timeout_task.cancel()
            self.timeout_task = None


class CIPublisherUmbBus(Module):
    """
    This module sends notifications of CI results via unified message bus.
    """

    name = 'publisher-umb-bus'
    description = 'Notification module - Unified message bus (UMB)'

    options = {
        'environment': {
            'help': 'Environment to send messages to.'
        },
        'certificate': {
            'help': 'Location of client cert for authentication.'
        },
        'ca': {
            'help': 'CA of the broker certificate.'
        },
        'topic_pattern': {
            'help': 'Topic pattern is merged together with test type and use as topic to send to'
        },
        'urls': {
            'help': 'Json file with urls'
        }
    }

    def execute(self):
        messages = self.shared('bus_messages') or {}

        if not messages:
            self.warn('No messages to send, did you call make-bus-messages module before this one?')

        with open(os.path.expanduser(self.option('urls'))) as data_file:
            urls = json.load(data_file)[self.option('environment')]

        for test_type in messages:
            if test_type in ['restraint', 'beaker']:
                topic_id = 'tier1'
            else:
                topic_id = test_type
            topic_id = re.sub('-', '.', topic_id)

            topic = self.option('topic_pattern').format(topic_id)
            one_type_messages = messages[test_type]
            messages_count = len(one_type_messages)
            self.info('Sending %s messages to %s', test_type, topic)

            for url in urls:
                self.info('Creating container for: %s', url)
                container = Container(TestHandler(self, url, one_type_messages, topic))
                container.run()

                if container.connected:
                    self.info('Container connected successfully to: %s', url)
                if one_type_messages:
                    self.warn('%s %s messages unsent', len(one_type_messages), test_type)
                else:
                    self.info('%s %s messages successfully sent', messages_count, test_type)
                    break
