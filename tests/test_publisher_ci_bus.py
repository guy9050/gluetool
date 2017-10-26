# pylint: disable=protected-access
import collections
import stomp
import pytest

from mock import MagicMock
import libci
import libci.modules.helpers.publisher_ci_bus
from libci.modules.helpers.publisher_ci_bus import CIBusPublisher
from . import create_module

Message = collections.namedtuple('Message', ('headers', 'body'))


@pytest.fixture(name='module')
def fixture_module():
    return create_module(CIBusPublisher, add_shared=False)


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
    python_mod = ci._load_python_module('testing/beaker', 'pytest_publisher_ci_bus',
                                        'libci/modules/helpers/publisher_ci_bus.py')

    assert hasattr(python_mod, 'CIBusPublisher')


def test_sanity(module):
    ci, module = module

    ci.has_shared('publish_bus_messages')


@pytest.mark.parametrize('dryrun_level', [
    libci.ci.DryRunLevels.DEFAULT,
    libci.ci.DryRunLevels.DRY,
    libci.ci.DryRunLevels.ISOLATED,
])
def test_dry_run_session(log, module, mock_stomp, dryrun_level):
    ci, module = module

    ci._dryrun_level = dryrun_level

    session = module._session

    if dryrun_level == libci.ci.DryRunLevels.ISOLATED:
        mock_stomp['Connection'].assert_not_called()
        assert log.match(message='Connecting to the message bus is not allowed by current dry-run level')
        assert session is None

    else:
        mock_stomp['Connection'].assert_called_once_with([(None, None)])
        mock_stomp['session.start'].assert_called_once()
        mock_stomp['session.connect'].assert_called_once_with(login=None, passcode=None, wait=True)
        mock_stomp['session.is_connected'].assert_called_once()
        assert session is mock_stomp['session']


def test_session_connect_fail(module, mock_stomp):
    _, module = module

    mock_stomp['session.connect'].side_effect = stomp.exception.ConnectFailedException('foo')

    with pytest.raises(libci.CIError, match=r'could not connect to CI message bus'):
        _ = module._session  # Ignore PyUnusedCodeBear


def test_session_not_connected(module, mock_stomp):
    _, module = module

    mock_stomp['session.is_connected'].return_value = False

    with pytest.raises(libci.CIError, match=r'could not connect to CI message bus'):
        _ = module._session  # Ignore PyUnusedCodeBear


@pytest.mark.parametrize('dryrun_level', [
    libci.ci.DryRunLevels.DEFAULT,
    libci.ci.DryRunLevels.DRY,
    libci.ci.DryRunLevels.ISOLATED,
])
def test_publish(log, module, mock_stomp, dryrun_level):
    ci, module = module

    ci._dryrun_level = dryrun_level

    message = Message(headers='dummy-headers', body={'foo': 'bar'})

    module.publish_bus_messages(message)

    assert log.match(message='sending the message')
    assert log.match(message='header:\n"dummy-headers"')
    assert log.match(message='body:\n{\n    "foo": "bar"\n}')

    if dryrun_level == libci.ci.DryRunLevels.DEFAULT:
        mock_stomp['session.send'].assert_called_once_with(body=libci.utils.format_dict(message.body),
                                                           headers=message.headers, destination=None)

    else:
        assert log.match(message='Sending messages to message bus is not allowed by current dry-run level')
