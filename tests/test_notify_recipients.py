import logging
import pytest

import libci
import libci.modules.helpers.notify_recipients

from . import Bunch, create_module


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.helpers.notify_recipients.NotifyRecipients)


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


def test_option_to_recipients_empty(module):
    _, mod = module

    # pylint: disable=protected-access
    mod._config['foo'] = None
    mod._config['bar'] = ''

    assert mod.option_to_recipients('foo') == []
    assert mod.option_to_recipients('bar') == []


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


def test_symbolic_recipients(module):
    ci, mod = module

    def fake_task():
        return Bunch(issuer='foo')

    # pylint: disable=protected-access
    ci._add_shared('brew_task', None, fake_task)

    assert mod.symbolic_recipients == {'ISSUER': 'foo'}


def test_recipients(module):
    _, mod = module

    # pylint: disable=protected-access
    mod._config.update({
        'foo-default-notify': ['def', 'ghi'],
        'foo-add-notify': ['jkl, abc', 'abc']
    })

    assert mod._recipients_by_result('foo') == ['def', 'ghi', 'jkl', 'abc', 'abc']


def test_notify_recipients(module):
    _, mod = module

    # pylint: disable=protected-access
    mod._config.update({
        'foo-notify': ['this', 'overrided', 'others'],
        'foo-default-notify': ['def', 'ghi'],
        'foo-add-notify': ['jkl, abc', 'abc']
    })

    assert mod._recipients_by_result('foo') == ['this', 'overrided', 'others']


def test_notify_force_recipients(module):
    _, mod = module

    # pylint: disable=protected-access
    mod._config.update({
        'force-recipients': ['even', 'more', 'powerful'],
        'foo-notify': ['this', 'overrided', 'others'],
        'foo-default-notify': ['def', 'ghi'],
        'foo-add-notify': ['jkl, abc', 'abc']
    })

    assert mod._recipients_by_result('foo') == ['even', 'more', 'powerful']


def test_overall_recipients(module):
    _, mod = module

    # pylint: disable=protected-access
    mod._config.update({
        'beaker-notify': ['def', 'ghi'],
        'boc-notify': ['pqr, zuv'],
        'restraint-notify': ['mno'],
        'rpmdiff-add-notify': ['jkl, abc', 'abc'],
        'covscan-default-notify': ['uvw'],
        'foo-notify': 'xyz'
    })

    assert mod._recipients_overall() == ['def', 'ghi', 'pqr', 'zuv', 'uvw', 'mno', 'jkl', 'abc', 'abc']


def test_finalize_recipients(log, module):
    _, mod = module

    mod.symbolic_recipients = {
        'FOO': 'some foo recipient',
    }

    # pylint: disable=protected-access
    assert mod._finalize_recipients(['foo', '{BAR}', 'bar', '{FOO}', 'baz']) \
        == ['bar', 'baz', 'foo', 'some foo recipient']
    assert log.records[0].message == "Cannot replace recipient '{BAR}' with the actual value"
    assert log.records[0].levelno == logging.WARN


def test_notification_recipients_overall(module):
    _, mod = module

    mod.symbolic_recipients = {
        'FOO': 'some foo recipient',
    }

    # pylint: disable=protected-access
    mod._config.update({
        'beaker-notify': ['def', 'ghi'],
        'boc-notify': ['pqr, {FOO}'],
        'restraint-notify': ['mno'],
        'rpmdiff-add-notify': ['jkl, abc', 'abc'],
        'covscan-default-notify': ['uvw'],
        'foo-notify': 'xyz'
    })

    assert mod.notification_recipients() == ['abc', 'def', 'ghi', 'jkl', 'mno', 'pqr', 'some foo recipient', 'uvw']


def test_notification_recipients_single(module):
    _, mod = module

    mod.symbolic_recipients = {
        'FOO': 'some foo recipient',
    }

    # pylint: disable=protected-access
    mod._config.update({
        'beaker-notify': ['def', 'ghi'],
        'boc-notify': ['pqr, {FOO}'],
        'restraint-notify': ['mno'],
        'rpmdiff-add-notify': ['jkl, abc', 'abc'],
        'covscan-default-notify': ['uvw'],
        'foo-notify': 'xyz'
    })

    assert mod.notification_recipients(result_type='boc') == ['pqr', 'some foo recipient']
