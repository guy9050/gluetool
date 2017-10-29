# pylint: disable=unused-import
import pytest

from gluetool.tests.conftest import fixture_enable_logger, fixture_enable_logger_propagate, fixture_log  # noqa
from libci.tests.conftest import fixture_module_with_primary_task  # noqa


def pytest_addoption(parser):
    parser.addoption('--integration-config', action='store', type=str, default=None,
                     help='Path to the real configuration')


@pytest.fixture(name='integration_config')
def fixture_integration_config(request):
    return request.config.getoption('--integration-config')
