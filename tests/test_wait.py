import re
import pytest

import libci
import libci.utils

from libci.utils import wait


def test_sanity(log):
    return_values = [False, False, True]

    wait('dummy check', lambda: return_values.pop(0), timeout=10, tick=2)

    assert len(log.records) == 7

    # todo: check decreasing remaining time
    # pylint: disable=line-too-long
    assert re.match(r"waiting for condition 'dummy check', timeout \d seconds, check every 2 seconds", log.records[0].message) is not None
    assert re.match(r'\d seconds left, sleeping for 2 seconds$', log.records[1].message) is not None
    assert log.records[2].message == 'check failed, assuming failure'
    assert re.match(r'\d seconds left, sleeping for 2 seconds$', log.records[3].message) is not None
    assert log.records[4].message == 'check failed, assuming failure'
    assert re.match(r'\d seconds left, sleeping for 2 seconds$', log.records[5].message) is not None
    assert log.records[6].message == 'check passed, assuming success'


def test_timeout():
    with pytest.raises(libci.CIError, match=r"Condition 'dummy check' failed to pass within given time"):
        wait('dummy check', lambda: False, timeout=2, tick=1)


def test_invalid_tick():
    with pytest.raises(libci.CIError, match=r'Tick must be an integer'):
        wait(None, None, tick=None)

    with pytest.raises(libci.CIError, match=r'Tick must be a positive integer'):
        wait(None, None, tick=-1)
