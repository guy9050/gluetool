import logging
import pytest

import gluetool
import gluetool_modules.helpers.notify_recipients

from mock import MagicMock

from . import create_module, patch_shared


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(gluetool_modules.helpers.notify_recipients.NotifyRecipients)[1]


@pytest.fixture(name='configured_module')
def fixture_configured_module(module, tmpdir):
    # This is a carefully constructed set of recipients, excercising different features
    # we want to test. Not all tests use the "configured" version of module, and of those
    # who does, not all check every of the recipient options, they are usually exclusive.

    # pylint: disable=protected-access
    module._config.update({
        'beaker-notify': ['def', 'ghi', 'to-be-removed-by-map'],
        'boc-notify': ['pqr, stu'],
        'restraint-notify': ['mno', 'some-weird/recipient'],
        'rpmdiff-analysis-add-notify': ['jkl, abc', 'abc'],
        'rpmdiff-comparison-add-notify': ['jkl, abc', 'abc'],
        'covscan-default-notify': ['uvw'],
        'foo-notify': 'xyz',
        'foo-default-notify': ['def', 'ghi'],
        'foo-add-notify': ['lkm', 'qwe, tgv'],
        'recipients': ['generic recipient 1, generic recipient 2', '  generic recipient 3  ']
    })

    map_file = tmpdir.join('dummy-map.yml')
    map_file.write("""---
- add-recipients:
    - from-map-always-foo
    - from-map-always-bar

- rule: BUILD_TARGET.match('dummy-target')
  add-recipients:
    - this won't make it to the list

- remove-recipients:
    - to-be-removed-by-map
""")

    module._config['recipients-map'] = str(map_file)

    return module


def test_sanity(module):
    pass


def test_loadable(module):
    # pylint: disable=protected-access
    python_mod = module.glue._load_python_module('helpers/notify-recipients', 'pytest_notify_recipients_job',
                                                 'gluetool_modules/helpers/notify_recipients.py')

    assert hasattr(python_mod, 'NotifyRecipients')


def test_deduplicate():
    deduplicate = gluetool_modules.helpers.notify_recipients.deduplicate

    # deduplicate returns items in arbitrary order, we must sort them
    assert sorted(deduplicate(('foo', 'bar', 'baz', 'baz', 'foo'))) == ['bar', 'baz', 'foo']


def test_polish():
    polish = gluetool_modules.helpers.notify_recipients.polish

    # polish returns sorted list, no need to sort the result - see test_deduplicate above
    assert polish(('foo', 'bar', 'baz', 'baz', 'foo')) == ['bar', 'baz', 'foo']


def test_force_recipients(module):
    # pylint: disable=protected-access
    module._config['force-recipients'] = ['foo', 'bar, baz']

    assert module.force_recipients == ['foo', 'bar', 'baz']


@pytest.mark.parametrize('target, expected', [
    # simple string should be wrapped by a list
    ('string-target', ['string-target']),
    # empty list is just an empty list
    ([], []),
    # teplates should be handled
    (['some {{ FOO }} ', 'or is it {{ FOO }}?'], ['some bar', 'or is it bar?'])
])
def test_prepare_target_recipients(module, target, expected):
    context = {
        'FOO': 'bar'
    }

    assert module._prepare_target_recipients(target, context) == expected


def test_add_mapped_recipients(module):
    # pylint: disable=line-too-long
    assert module._add_recipients(['foo', 'bar'], {'VAR': 'baz'}, ['simple baz', 'complicated {{ VAR }}']) == ['foo', 'bar', 'simple baz', 'complicated baz']  # Ignore PEP8Bear


def test_remove_mapped_recipients(module):
    # pylint: disable=line-too-long
    assert module._remove_recipients(['foo', 'bar', 'simple baz', 'complicated baz'], {'VAR': 'baz'}, ['simple baz', 'complicated {{ VAR }}']) == ['foo', 'bar']  # Ignore PEP8Bear


def test_replace_mapped_recipients(module):
    # pylint: disable=line-too-long
    assert module._replace_recipients(['foo', 'simple baz', 'complicated baz'], {'VAR': 'baz'}, '.*? baz', ['just {{ VAR }}'])  # Ignore PEP8Bear


@pytest.mark.parametrize('source, target, error_message', [
    ('bar', None, "Don't know what to use instead of 'bar'"),
    ('[', '', r"Cannot compile pattern '.*?': unexpected end of regular expression")
])
def test_replace_mapped_recipients_error(module, source, target, error_message):
    with pytest.raises(gluetool.GlueError, match=error_message):
        module._replace_recipients([], {}, source, target)


def test_recipients_map(configured_module, monkeypatch):
    patch_shared(monkeypatch, configured_module, {
        'eval_context': {},
        'evaluate_rules': False
    })

    monkeypatch.setattr(configured_module, '_add_recipients', MagicMock(return_value=[]))
    monkeypatch.setattr(configured_module, '_remove_recipients', MagicMock(return_value=[]))
    monkeypatch.setattr(configured_module, '_replace_recipients', MagicMock(return_value=[]))

    assert configured_module._apply_recipients_map([]) == []

    configured_module._add_recipients.assert_called_once()
    configured_module._remove_recipients.assert_called_once()
    configured_module._remove_recipients.assert_called_once()


def test_recipients(configured_module):
    """
    Tests whether *-default-notify and *-add-notify are correctly merged.
    """

    # Configured module has foo-notify set, and that'd override our test
    # pylint: disable=protected-access
    del configured_module._config['foo-notify']

    assert configured_module._recipients_by_result('foo') == [
        'def', 'ghi', 'lkm', 'qwe', 'tgv',
        'generic recipient 1', 'generic recipient 2', 'generic recipient 3'
    ]


def test_notify_recipients(configured_module):
    """
    Tests whether ``*-notify`` overrides ``*-default-notify``, ``*-add-notify`` and ``recipients``.
    """

    # pylint: disable=protected-access
    assert configured_module._recipients_by_result('foo') == [
        'xyz'
    ]


def test_notify_force_recipients(configured_module):
    """
    Tests whether force-recipients overrides result-specific options.
    """

    # pylint: disable=protected-access
    configured_module._config['force-recipients'] = ['even', 'more', 'powerful']

    assert configured_module._recipients_by_result('foo') == [
        'even', 'more', 'powerful'
    ]


def test_overall_recipients(configured_module):
    """
    Tests whether it's possible to get recipients for all known result types.
    """

    # pylint: disable=protected-access
    assert configured_module._recipients_overall() == [
        'def', 'ghi', 'to-be-removed-by-map', 'pqr', 'stu',
        'generic recipient 1', 'generic recipient 2', 'generic recipient 3',
        'uvw',
        'generic recipient 1', 'generic recipient 2', 'generic recipient 3',
        'mno', 'some-weird/recipient', 'jkl', 'abc', 'abc',
        'generic recipient 1', 'generic recipient 2', 'generic recipient 3',
        'jkl', 'abc', 'abc',
        'generic recipient 1', 'generic recipient 2', 'generic recipient 3'
    ]


def test_finalize_recipients(log, configured_module, monkeypatch):
    """
    Tests finalization of recipient lists.
    """

    mock_task = MagicMock(targe='dummy-target')

    patch_shared(monkeypatch, configured_module, {
        'eval_context': {
            'PRIMARY_TASK': mock_task,
            'TASKS': [mock_task]
        }
    })

    # pylint: disable=protected-access
    assert configured_module._finalize_recipients(['foo', 'bar', 'baz', 'to-be-removed-by-map']) \
        == ['bar', 'baz', 'foo', 'from-map-always-bar', 'from-map-always-foo']


@pytest.mark.parametrize('result_type,expected_recipients', [
    # Without result type, return all recipients
    (
        None,
        # pylint: disable=line-too-long
        ['abc', 'def', 'from-map-always-bar', 'from-map-always-foo', 'generic recipient 1', 'generic recipient 2', 'generic recipient 3', 'ghi', 'jkl', 'mno', 'pqr', 'some-weird/recipient', 'stu', 'uvw']  # Ignore PEP8Bear
    ),
    # With specific type, return just its recipients
    ('beaker', ['def', 'from-map-always-bar', 'from-map-always-foo', 'ghi']),
    ('boc', ['from-map-always-bar', 'from-map-always-foo', 'pqr', 'stu']),
    ('covscan', ['from-map-always-bar', 'from-map-always-foo',
                 'generic recipient 1', 'generic recipient 2', 'generic recipient 3', 'uvw']),
    ('foo', ['from-map-always-bar', 'from-map-always-foo', 'xyz']),
    ('restraint', ['from-map-always-bar', 'from-map-always-foo', 'mno', 'some-weird/recipient']),
    ('rpmdiff-analysis', ['abc', 'from-map-always-bar', 'from-map-always-foo',
                          'generic recipient 1', 'generic recipient 2', 'generic recipient 3', 'jkl']),
    ('rpmdiff-comparison', ['abc', 'from-map-always-bar', 'from-map-always-foo',
                            'generic recipient 1', 'generic recipient 2', 'generic recipient 3', 'jkl'])
])
def test_notification_recipients_overall(configured_module, monkeypatch, result_type, expected_recipients):
    """
    Tests whether correct recipients are returned for different result types.
    """

    mock_task = MagicMock(targe='dummy-target')
    patch_shared(monkeypatch, configured_module, {
        'eval_context': {
            'PRIMARY_TASK': mock_task,
            'TASKS': [mock_task]
        }
    })

    assert configured_module.notification_recipients(result_type=result_type) == expected_recipients
