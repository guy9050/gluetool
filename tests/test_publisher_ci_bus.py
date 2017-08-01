# pylint: disable=protected-access
import collections
import stomp
import pytest

from mock import MagicMock
import libci
from libci.modules.helpers.publisher_ci_bus import CIPublisherCiBus
from . import create_module

Message = collections.namedtuple('Message', 'headers body')

HEADERS = 'dummy_headers'
BODY = 'dummy_body'

MESSAGES = {'covscan': [Message(headers=HEADERS, body=BODY)],
            'rpmdiff': [Message(headers=HEADERS, body=BODY), Message(headers=HEADERS, body=BODY)]}


def was_called(log, message):
    return any(record.message == message for record in log.records)


@pytest.fixture(name='module')
def fixture_module():
    return create_module(CIPublisherCiBus)


def test_loadable(module):
    ci, _ = module
    # pylint: disable=protected-access
    python_mod = ci._load_python_module('testing/beaker', 'pytest_publisher_ci_bus',
                                        'libci/modules/helpers/publisher_ci_bus.py')

    assert hasattr(python_mod, 'CIPublisherCiBus')


def test_no_messages(log, module, monkeypatch):
    _, module = module

    def mocked_shared(key):
        return {
            'bus_messages': None
        }[key]

    monkeypatch.setattr(module, 'shared', mocked_shared)
    monkeypatch.setattr(module, 'publish', MagicMock())

    module.execute()

    assert log.records[-1].message == 'No messages to send, did you call make-bus-messages module before this one?'
    module.publish.assert_not_called()


def test_dry_run(log, module, monkeypatch):
    _, module = module
    module._config['dry-run'] = True
    module._config['destination'] = 'dummy_destination'

    mocked_send = MagicMock()

    monkeypatch.setattr(module, 'shared', MagicMock(return_value=MESSAGES))
    monkeypatch.setattr(module, 'cibus', MagicMock(send=mocked_send))

    module.execute()

    assert was_called(log, 'running in dry-run mode, no messages will be sent out')
    mocked_send.assert_not_called()


def test_run(log, module, monkeypatch):
    _, module = module
    module._config['dry-run'] = False
    module._config['destination'] = 'dummy_destination'

    mocked_send = MagicMock()

    monkeypatch.setattr(module, 'shared', MagicMock(return_value=MESSAGES))
    monkeypatch.setattr(module, 'cibus', MagicMock(send=mocked_send))

    module.execute()

    mocked_send.assert_called()
    assert was_called(log, '1 covscan message published to CI message bus')
    assert was_called(log, '2 rpmdiff messages published to CI message bus')

    monkeypatch.setattr(stomp, '__version__', MagicMock(__version__=[3]))

    module.execute()

    mocked_send.assert_called()
    assert was_called(log, '1 covscan message published to CI message bus')
    assert was_called(log, '2 rpmdiff messages published to CI message bus')


def test_sanity_dry_run(module, monkeypatch):
    _, module = module
    module._config['dry-run'] = True

    mocked_start = MagicMock()
    mocked_cibus = MagicMock(send=mocked_start)
    monkeypatch.setattr(module, 'cibus', mocked_cibus)

    module.sanity()

    mocked_start.assert_not_called()


def test_sanity_not_connected(module, monkeypatch):
    _, module = module
    module._config['dry-run'] = False
    module._config['host'] = 'dummy_host'
    module._config['port'] = 'dummy_port'
    module._config['user'] = 'dummy_user'
    module._config['password'] = 'dummy_password'

    mocked_connected = MagicMock(return_value=False)
    mocked_cibus = MagicMock(connect=MagicMock(), is_connected=mocked_connected)

    monkeypatch.setattr(stomp, 'Connection', MagicMock(return_value=mocked_cibus))

    with pytest.raises(libci.CIError, match=r"^could not connect to CI message bus"):
        module.sanity()


def test_sanity_connection_error(module, monkeypatch):
    _, module = module
    module._config['dry-run'] = False
    module._config['host'] = 'dummy_host'
    module._config['port'] = 'dummy_port'
    module._config['user'] = 'dummy_user'
    module._config['password'] = 'dummy_password'

    def mocked_connect(**kwargs):
        # pylint: disable=unused-argument
        raise stomp.exception.ConnectFailedException

    mocked_connected = MagicMock(return_value=True)
    mocked_cibus = MagicMock(connect=mocked_connect, is_connected=mocked_connected)

    monkeypatch.setattr(stomp, 'Connection', MagicMock(return_value=mocked_cibus))

    with pytest.raises(libci.CIError, match=r"^could not connect to CI message bus"):
        module.sanity()
