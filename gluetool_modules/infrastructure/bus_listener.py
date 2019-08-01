import collections
import json
import os
import re
import signal
import sys

import stomp

import gluetool
from gluetool.log import LoggerMixin, format_dict


DEFAULT_BUS_HOST = 'ci-bus.lab.eng.rdu2.redhat.com'
DEFAULT_BUS_PORT = 61613
DEFAULT_DESTINATION = '/topic/CI'


class Listener(LoggerMixin, stomp.listener.ConnectionListener):
    """
    Listener we pass to Stomp to react on received messages.

    We need to perform multiple actions when a single message arrives
    but Stomp API allows only a single listener. Therefore this listener
    class provides simple callback-based approach - users of this module
    can register callbacks with specific events, and listener will take
    care of calling them with proper arguments when events fire.

    See http://jasonrbriggs.github.io/stomp.py/stomp.html#module-stomp.listener
    for supported events - all ``on_*`` methods of ``ConnectionListener`` class
    represent events - for example, user can add a callback to ``on_error`` event.

    Callback receives following arguments:

    - listener (``Listener`` instance) that fired the callback,
    - arguments declared for the respective ``on_*`` method of ``ConnectionListener`` class, e.g.
      message headers (``dict``) and message body (``dict``) in the case of ``on_message`` event.

    It is also possible to supply additional arguments, positional and keyword,
    when registering a callback.
    """

    def __init__(self, module):
        super(Listener, self).__init__(module.logger)

        self._module = module

        self.keep_running = True

        # callbacks are processed in a separate thread, therefore exceptions raised there
        # won't make it into the main thread.
        self.exc_info = None

        self._callbacks = collections.defaultdict(dict)

    def add_callback(self, event, name, callback, *args, **kwargs):
        """
        Add a callback for an event.

        Optional positional and keyword arguments are passed to every call
        of ``callback``.

        :param str event: Event name.
        :param str name: Name of the callback. Used for its removal and logging purposes.
        :param callable callback: Function to call when event fires.
        """

        self.info("adding callback '{}' for event '{}'".format(name, event))
        self.debug('callback={}, args={}, kwargs={}'.format(callback, args, kwargs))

        self._callbacks[event][name] = (callback, args, kwargs)

    def remove_callback(self, event, name):
        """
        Remove a callback for an event.

        :param str event: Event name.
        :param str name: Name used when callback was registered.
        """

        self.info("removing callback '{}' for event '{}".format(name, event))

        self._callbacks[event].pop(name, None)

    def stop(self):
        """
        Signal listener to stop listening. Used in conjunction with :py:meth:`BusListener.bus_accept`.
        """

        self.keep_running = False
        os.kill(os.getpid(), signal.SIGUSR1)

    def _dispatch_callbacks(self, event, *args, **kwargs):
        """
        Dispatch callbacks for an event.
        """

        self.debug('callbacks:\n{}'.format(format_dict(self._callbacks[event])))

        for callback, preset_args, preset_kwargs in self._callbacks[event].itervalues():
            actual_args = (self,) + args + preset_args
            actual_kwargs = dict(kwargs, **preset_kwargs)

            try:
                callback(*actual_args, **actual_kwargs)

            # pylint: disable=broad-except
            except Exception:
                self.exc_info = sys.exc_info()
                self.stop()

    def on_error(self, headers, body):
        self.debug("'on_error' event raised, dispatching callbacks")

        # errors' bodies are not guaranteed to be a JSON, therefore pass it further
        # in their raw form.
        self._dispatch_callbacks('on_error', headers, body)

    def on_message(self, headers, body):
        self.debug("'on_message' event raised, dispatching callbacks")

        self._dispatch_callbacks('on_message', headers, json.loads(body))


class BusListener(gluetool.Module):
    """
    Provide simple access to a message bus. Using callbacks, users of this
    module can react on received messages.
    """

    name = 'bus-listener'
    description = 'Generic, event-based bus listening module.'

    options = {
        'user': {
            'help': 'Username to use to connect to the message bus.'
        },
        'password': {
            'help': 'Password to use to connect to the message bus.'
        },
        'host': {
            'help': 'Message bus host (default: %(default)s).',
            'default': DEFAULT_BUS_HOST
        },
        'port': {
            'help': 'Message bus port (default: %(default)s).',
            'default': DEFAULT_BUS_PORT,
            'type': int
        },
        'selector': {
            'help': 'JMS selector for filtering messages. Can be used multiple times (default: none).',
            'dest': 'selectors',
            'action': 'append',
            'default': []
        },
        'destination': {
            'help': 'Message bus topic/subscription (default: %(default)s).',
            'default': DEFAULT_DESTINATION
        },
        'queue-directory': {
            'help': 'Store incomming messages into DIR',
            'metavar': 'DIR'
        },
        'count': {
            'help': 'Quit after receiving N messages.',
            'metavar': 'N',
            'type': int
        },
        'listen': {
            'help': 'After setup, enter endless listening mode.',
            'action': 'store_true'
        }
    }

    required_options = ('user', 'password')
    shared_functions = ('add_bus_callback', 'remove_bus_callback', 'bus_accept')

    _queue_directory = None
    _count = None

    def add_bus_callback(self, *args, **kwargs):
        assert self._listener is not None

        self._listener.add_callback(*args, **kwargs)

    def remove_bus_callback(self, *args, **kwargs):
        assert self._listener is not None

        self._listener.remove_callback(*args, **kwargs)

    def bus_accept(self):
        """
        Start endless loop, accepting incoming messages. The loop can be interrupted
        by calling listener's :py:meth:`Listener.stop` from a callback function.
        """

        self.debug('endless listening on bus')

        while self._listener.keep_running:
            signal.pause()

    def _store_message(self, listener, headers, body):
        """
        Store message (including headers) into a file in the directory user
        specified using ``--queue-directory`` option.
        """

        # pylint: disable=unused-argument

        msg_id = headers.get('message-id', None)

        if msg_id is None:
            self.warn("Cannot find 'message-id' key in headers, don't know how to name queue file")
            return

        path = os.path.join(self._queue_directory, '{}.json'.format(msg_id))

        with open('{}.json'.format(path), 'w') as f:
            f.write(format_dict({
                'headers': headers,
                'body': body
            }))

            f.flush()

        self.debug("message '{}' stored in {}".format(msg_id, path))

    def _dump_message(self, listener, headers, body):
        """
        Dump message and its headers into debug log.
        """

        # pylint: disable=unused-argument

        self.debug("message received:\nheaders: {}\nmessage: {}".format(format_dict(headers), format_dict(body)))

    def _on_error(self, listener, headers, body):
        # pylint: disable=unused-argument

        if 'message' in headers and re.match(r'User name \[.*?\] or password is invalid\.', headers['message']):
            raise gluetool.GlueError('Invalid username or password')

        self.debug("'ERROR' frame received:\nheaders: {}\nmessage: {}".format(format_dict(headers), body))

    def _quit_after_n_messages(self, listener, headers, body):
        """
        Stop receiving messages after receiving specified number of messages. The number
        is set by user via ``--count`` option.
        """

        # pylint: disable=unused-argument

        self._count -= 1

        if self._count > 0:
            return

        listener.stop()

    def execute(self):
        # pylint: disable=attribute-defined-outside-init

        try:
            self._connection = conn = stomp.Connection([(self.option('host'), self.option('port'))])
            self._listener = listener = Listener(self)

            conn.set_listener('Bus Listener', listener)
            conn.start()
            conn.connect(login=self.option('user'), passcode=self.option('password'))

            if self.option('selector'):
                for i, selector in enumerate(self.option('selector')):
                    self.info("subscribing with selector '{}'".format(selector))

                    conn.subscribe(destination=self.option('destination'),
                                   id='selector #{}'.format(i),
                                   ack='auto',
                                   headers={'selector': selector})

            else:
                self.info('subscribing without specific selector')

                conn.subscribe(destination=self.option('destination'),
                               id='catch-all selector',
                               ack='auto')

        except stomp.exception.ConnectFailedException as exc:
            raise gluetool.GlueError('Unable to connect to the message bus: {}'.format(str(exc)))

        except stomp.exception.StompException as exc:
            raise gluetool.GlueError('Exception raised when connecting to message bus: {}'.format(exc))

        self.add_bus_callback('on_error', 'dump message', self._on_error)
        self.add_bus_callback('on_message', 'dump message', self._dump_message)

        if self.option('queue-directory'):
            self._queue_directory = gluetool.utils.normalize_path(self.option('queue-directory'))
            self.add_bus_callback('on_message', 'store message in queue directory', self._store_message)

        if self.option('count'):
            self._count = self.option('count')
            self.add_bus_callback('on_message', 'quit after N messages', self._quit_after_n_messages)

        if self.option('listen'):
            self.bus_accept()

        if listener.exc_info is not None:
            raise listener.exc_info[1]
