from collections import namedtuple
import pytest

from libci.modules.helpers.rpmdiff_waiver import RpmDiffError, RpmDiffWaiverMatcher


Waiver = namedtuple("Waiver", "id subpackage content_pattern test")


@pytest.fixture(name='errors')
def fixture_errors():
    return [
        RpmDiffError("Failed", "testsubpackage", "Unassigned int"),
        RpmDiffError("Failed", "testsubpackage", "Unassigned long")
    ]


@pytest.fixture(name='matcher')
def fixture_matcher(errors):
    waivers = [
        Waiver("1", "testsubpackage", "Unassigned .*", "testname")
    ]
    return RpmDiffWaiverMatcher(errors, waivers)


@pytest.fixture(name='matcher_multiple_waivers')
def fixture_matcher_multiple_waivers(errors):
    waivers = [
        Waiver("1", "testsubpackage", "Unassigned int", "testname"),
        Waiver("1", "testsubpackage", "Unassigned long", "testname"),
    ]
    return RpmDiffWaiverMatcher(errors, waivers)


@pytest.mark.parametrize("pattern", [".*", "Unassigned .*", "^Unassigned"])
def test_rpmdiff_waiver_matcher_success(matcher, pattern):
    matcher.waivers[0] = Waiver("1", "testsubpackage", pattern, "testname")
    assert matcher.can_waive()


def test_rpmdiff_waiver_matcher_success_multiple_waivers(matcher_multiple_waivers):
    # pylint: disable=invalid-name
    assert matcher_multiple_waivers.can_waive()


@pytest.mark.parametrize("subpackage", ["test", "", None, "dummy"])
def test_rpmdiff_waiver_matcher_fail_subpackage(matcher, subpackage):
    # pylint: disable=invalid-name
    matcher.waivers[0] = Waiver("1", subpackage, "Unassigned .*", "testname")
    assert not matcher.can_waive()


@pytest.mark.parametrize("pattern", ["unassigned .*", "A.*", "^Unad", None, "", "Unassigned int"])
def test_rpmdiff_waiver_matcher_fail_content(matcher, pattern):
    matcher.waivers[0] = Waiver("1", "testsubpackage", pattern, "testname")
    assert not matcher.can_waive()


def test_rpmdiff_waiver_matcher_only_one_matched(matcher_multiple_waivers):
    # pylint: disable=invalid-name
    matcher = matcher_multiple_waivers
    matcher.waivers.pop(1)
    assert not matcher.can_waive()
