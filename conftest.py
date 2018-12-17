# pylint: disable=unused-import
import mock
import pytest

from gluetool.tests.conftest import fixture_enable_logger, fixture_enable_logger_propagate, fixture_log  # noqa
from libci.tests.conftest import fixture_module_with_primary_task  # noqa


def pytest_addoption(parser):
    parser.addoption('--integration-config', action='store', type=str, default=None,
                     help='Path to the real configuration')


@pytest.fixture(name='integration_config')
def fixture_integration_config(request):
    return request.config.getoption('--integration-config')


@pytest.fixture(name='mock_command')
def fixture_mock_command(monkeypatch):
    """
    Mock :py:ref:`gluetool.utils.Command` and its ``run`` method.

    :returns: tuple of 4 items:
        * a mock representing ``Command()`` call,
        * a mock representing new ``Command`` instance, returned by this ``Command()`` call,
        * a mock representing ``run`` method of this instance, and
        * a mock output returned when this ``run()`` is called.
    """

    # `Command.run` return value
    mock_output = mock.MagicMock(exit_code=0, stdout='dummy stdout', stderr='dummy stderr')

    # `Command.run` method
    mock_run = mock.MagicMock(return_value=mock_output)

    # `Command` instance
    mock_command = mock.MagicMock(run=mock_run)

    # `Command()` mock
    mock_command_init = mock.MagicMock(return_value=mock_command)

    import gluetool
    monkeypatch.setattr(gluetool.utils, 'Command', mock_command_init)

    return mock_command_init, mock_command, mock_run, mock_output