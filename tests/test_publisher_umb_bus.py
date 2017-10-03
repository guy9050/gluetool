# pylint: disable=protected-access
import collections
import pytest

import proton
import proton.handlers
import proton.reactor

from mock import MagicMock

import libci
import libci.modules.helpers.publisher_umb_bus

from . import create_module

Message = collections.namedtuple('Message', ('headers', 'body'))


@pytest.fixture(name='module')
def fixture_module():
    ci, module = create_module(libci.modules.helpers.publisher_umb_bus.UMBPublisher)

    module._urls = ['dummy-url #1', 'dummy-url #2']

    return ci, module


@pytest.fixture(name='mock_stomp')
def fixture_mock_stomp(monkeypatch):
    mock_start = MagicMock()
    mock_connect = MagicMock()
    mock_is_connected = MagicMock(return_value=True)
    mock_send = MagicMock()
    mock_session = MagicMock(start=mock_start, connect=mock_connect, is_connected=mock_is_connected, send=mock_send)

    mock_connection = MagicMock(return_value=mock_session)

    monkeypatch.setattr(stomp, 'Connection', mock_connection)

    return {
        'Connection': mock_connection,
        'session': mock_session,
        'session.start': mock_start,
        'session.connect': mock_connect,
        'session.is_connected': mock_is_connected,
        'session.send': mock_send
    }


def test_loadable(module):
    ci, _ = module

    # pylint: disable=protected-access
    python_mod = ci._load_python_module('testing/beaker', 'pytest_publisher_umb_bus',
                                        'libci/modules/helpers/publisher_umb_bus.py')

    assert hasattr(python_mod, 'UMBPublisher')


def test_sanity(module):
    ci, module = module

    ci.has_shared('publish_bus_messages')


def test_publish(module, monkeypatch):
    _, module = module

    module._environment = {
        'urls': [
            'dummy-broker-#1',
            'dummy-broker-#2'
        ]
    }

    message = Message(headers='dummy-headers', body={'foo': 'bar'})

    mock_container = MagicMock()
    monkeypatch.setattr(proton.reactor, 'Container', mock_container)

    with pytest.raises(libci.CIError, match=r'Could not send all the messages, 1 remained\.'):
        module.publish_bus_messages(message)

    mock_container.assert_called()
