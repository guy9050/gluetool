# pylint: disable=blacklisted-name

import pytest

import libci

from . import CaplogWrapper


def pytest_addoption(parser):
    parser.addoption('--integration-config', action='store', default=str, help='Path to the real configuration')


@pytest.fixture(name='integration_config')
def fixture_integration_config(request):
    return request.config.getoption('--integration-config')


@pytest.fixture(name='logger', scope='session', autouse=True)
def fixture_enable_logger():
    """
    Initialize logger - in ``libci``, this is done by :py:class:`libci.ci.CI` instance
    but we don't have such luxury in the ``libci`` unit tests.
    """

    return libci.Logging.create_logger()


@pytest.fixture(name='enable_logger_propagate', scope='session', autouse=True)
def fixture_enable_logger_propagate():
    """
    Allow propagation of logging records to logger's parents. Without this step, log capturing would
    not work as it sets up another logger, capturing messages propagated by our "real" loggers.
    """

    libci.Logging.create_logger().propagate = True


@pytest.fixture(name='log', scope='function')
def fixture_log(caplog):
    """
    Wrap the original ``caplog`` object with our proxy that resets "the environment" by clearing
    records captured so far.
    """

    wrapper = CaplogWrapper(caplog)
    wrapper.clear()
    return wrapper
