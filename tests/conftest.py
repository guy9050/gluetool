# pylint: disable=blacklisted-name

import pytest

import libci


# Without this, caplog cannot "see" citool's messages since they are
# no longer propagated to citool logger's parent(s)
@pytest.fixture(name='enable_logger_propagate', scope='session', autouse=True)
def fixture_enable_logger_propagate():
    libci.Logging.create_logger().propagate = True
