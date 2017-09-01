import logging
import pytest

import libci
import libci.modules.helpers.notify_recipients

from mock import MagicMock

from . import create_module, patch_shared


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.helpers.notify_recipients.NotifyRecipients)


@pytest.fixture(name='configured_module')
def fixture_configured_module(module, tmpdir):
    ci, mod = module

    # This is a carefully constructed set of recipients, excercising different features
    # we want to test. Not all tests use the "configured" version of module, and of those
    # who does, not all check every of the recipient options, they are usually exclusive.

    # pylint: disable=protected-access
    mod._config.update({
        'beaker-notify': ['def', 'ghi'],
        'boc-notify': ['pqr, {FOO}'],
        'restraint-notify': ['mno', 'some-weird/recipient'],
        'rpmdiff-analysis-add-notify': ['jkl, abc', 'abc'],
        'rpmdiff-comparison-add-notify': ['jkl, abc', 'abc'],
        'covscan-default-notify': ['uvw'],
        'foo-notify': 'xyz',
        'foo-default-notify': ['def', 'ghi'],
        'foo-add-notify': ['lkm', 'qwe, tgv']
    })

    mod.symbolic_recipients = {
        'FOO': 'some foo recipient'
    }

    map_file = tmpdir.join('dummy-map.yml')
    map_file.write("""---
- 'some-weird/recipient': 'the real one!; and another real one!'
""")

    mod._config['mapped-recipients-map'] = str(map_file)

    return ci, mod


def test_sanity(module):
    _, _ = module


def test_loadable(module):
    ci, _ = module

    # pylint: disable=protected-access
    python_mod = ci._load_python_module('helpers/notify-recipients', 'pytest_notify_recipients_job',
                                        'libci/modules/helpers/notify_recipients.py')

    assert hasattr(python_mod, 'NotifyRecipients')


def test_deduplicate():
    deduplicate = libci.modules.helpers.notify_recipients.deduplicate

    # deduplicate returns items in arbitrary order, we must sort them
    assert sorted(deduplicate(('foo', 'bar', 'baz', 'baz', 'foo'))) == ['bar', 'baz', 'foo']


def test_polish():
    polish = libci.modules.helpers.notify_recipients.polish

    # polish returns sorted list, no need to sort the result - see test_deduplicate above
    assert polish(('foo', 'bar', 'baz', 'baz', 'foo')) == ['bar', 'baz', 'foo']


@pytest.mark.parametrize('recipients', [None, ''])
def test_option_to_recipients_empty(module, recipients):
    _, mod = module

    # pylint: disable=protected-access
    mod._config['foo'] = recipients

    assert mod.option_to_recipients('foo') == []


def test_option_to_recipients_multiple(module):
    _, mod = module

    # pylint: disable=protected-access
    mod._config['foo'] = ['foo', 'bar, baz']

    assert mod.option_to_recipients('foo') == ['foo', 'bar', 'baz']


def test_force_recipients(module):
    _, mod = module

    # pylint: disable=protected-access
    mod._config['force-recipients'] = ['foo', 'bar, baz']

    assert mod.force_recipients == ['foo', 'bar', 'baz']


def test_symbolic_recipients_no_task(module):
    _, mod = module

    assert mod.symbolic_recipients == {}


def test_symbolic_recipients(monkeypatch, module):
    _, mod = module

    patch_shared(monkeypatch, mod, {
        'primary_task': MagicMock(issuer='foo')
    })

    # pylint: disable=protected-access
    assert mod._replace_symbolic_recipients(['bar', '{ISSUER}']) == ['bar', 'foo']


def test_mapped_recipients_map(configured_module):
    _, mod = configured_module

    assert isinstance(mod.mapped_recipients, libci.utils.PatternMap)
    assert mod.mapped_recipients.match('some-weird/recipient') == 'the real one!; and another real one!'


def test_mapped_recipients_map_unset(module):
    _, mod = module

    assert mod.mapped_recipients is None
    # pylint: disable=protected-access
    assert mod._replace_mapped_recipients(['bar', 'some-weird/recipient']) == ['bar', 'some-weird/recipient']


def test_mapped_recipients(configured_module):
    _, mod = configured_module

    # pylint: disable=protected-access,line-too-long
    assert mod._replace_mapped_recipients(['bar', 'some-weird/recipient']) == ['bar', 'the real one!', 'and another real one!']


def test_recipients(configured_module):
    """
    Tests whether *-default-notify and *-add-notify are correctly merged.
    """

    _, mod = configured_module

    # Configured module has foo-notify set, and that'd override our test
    # pylint: disable=protected-access
    del mod._config['foo-notify']

    assert mod._recipients_by_result('foo') == ['def', 'ghi', 'lkm', 'qwe', 'tgv']


def test_notify_recipients(configured_module):
    """
    Tests whether *-notify overrides *-default-notify and *-add-notify.
    """

    _, mod = configured_module

    # pylint: disable=protected-access
    assert mod._recipients_by_result('foo') == ['xyz']


def test_notify_force_recipients(configured_module):
    """
    Tests whether force-recipients overrides result-specific options.
    """

    _, mod = configured_module

    # pylint: disable=protected-access
    mod._config['force-recipients'] = ['even', 'more', 'powerful']

    assert mod._recipients_by_result('foo') == ['even', 'more', 'powerful']


def test_overall_recipients(configured_module):
    """
    Testes whether it's possible to get recipients for all known result types.
    """

    _, mod = configured_module

    # pylint: disable=protected-access
    assert mod._recipients_overall() == ['def', 'ghi', 'pqr', '{FOO}', 'uvw', 'mno', 'some-weird/recipient', 'jkl',
                                         'abc', 'abc', 'jkl', 'abc', 'abc']


def test_finalize_recipients(log, configured_module):
    """
    Tests finalization of recipient lists.
    """

    _, mod = configured_module

    # pylint: disable=protected-access
    assert mod._finalize_recipients(['foo', '{BAR}', 'bar', '{FOO}', 'baz']) \
        == ['bar', 'baz', 'foo', 'some foo recipient']
    assert log.records[0].message == "Cannot replace symbolic recipient '{BAR}'"
    assert log.records[0].levelno == logging.WARN


@pytest.mark.parametrize('result_type,expected_recipients', [
    # Without result type, return all recipients
    # pylint: disable=line-too-long
    (None, ['abc', 'and another real one!', 'def', 'ghi', 'jkl', 'mno', 'pqr', 'some foo recipient', 'the real one!', 'uvw']),
    # With specific type, return just its recipients
    ('beaker', ['def', 'ghi']),
    ('boc', ['pqr', 'some foo recipient']),
    ('covscan', ['uvw']),
    ('foo', ['xyz']),
    ('restraint', ['and another real one!', 'mno', 'the real one!']),
    ('rpmdiff-analysis', ['abc', 'jkl']),
    ('rpmdiff-comparison', ['abc', 'jkl'])
])
def test_notification_recipients_overall(configured_module, result_type, expected_recipients):
    """
    Tests whether correct recipients are returned for different result types.
    """

    _, mod = configured_module

    assert mod.notification_recipients(result_type=result_type) == expected_recipients
