# pylint: disable=protected-access
import collections
import pytest

import proton
import proton.handlers
import proton.reactor

from mock import MagicMock

import gluetool
import gluetool_modules.helpers.publisher_umb_bus

from . import create_module

Message = collections.namedtuple('Message', ('headers', 'body'))


@pytest.fixture(name='module')
def fixture_module():
    ci, module = create_module(gluetool_modules.helpers.publisher_umb_bus.UMBPublisher)

    module._urls = ['dummy-url #1', 'dummy-url #2']

    return ci, module


def test_loadable(module):
    ci, _ = module

    # pylint: disable=protected-access
    python_mod = ci._load_python_module('testing/beaker', 'pytest_publisher_umb_bus',
                                        'gluetool_modules/helpers/publisher_umb_bus.py')

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

    with pytest.raises(gluetool.GlueError, match=r'Could not send all the messages, 1 remained\.'):
        module.publish_bus_messages(message)

    mock_container.assert_called()
