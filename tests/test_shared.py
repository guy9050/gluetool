# pylint: disable=blacklisted-name

import pytest

from mock import MagicMock

import libci

from . import NonLoadingCI, create_module


class DummyModule(libci.Module):
    """
    Dummy module, implementing necessary methods and attributes
    to pass through CI's internal machinery.
    """

    name = 'Dummy module'

    def foo(self):
        pass


@pytest.fixture(name='ci')
def fixture_ci():
    return NonLoadingCI()


@pytest.fixture(name='module')
def fixture_module():
    return create_module(DummyModule)[1]


def test_core_add_shared(ci):
    module = MagicMock()
    func = MagicMock()

    # pylint: disable=protected-access
    ci._add_shared('dummy_func', module, func)

    assert ci.shared_functions['dummy_func'] == (module, func)


def test_add_shared(ci, monkeypatch):
    dummy_func = MagicMock()
    module = MagicMock(dummy_func=dummy_func)

    _add_shared = MagicMock()

    monkeypatch.setattr(ci, '_add_shared', _add_shared)

    ci.add_shared('dummy_func', module)

    _add_shared.assert_called_once_with('dummy_func', module, dummy_func)


def test_add_shared_missing(ci):
    module = MagicMock(spec=libci.Module)
    module.name = 'dummy_module'

    with pytest.raises(libci.CIError, match=r"No such shared function 'dummy_func' of module 'dummy_module'"):
        ci.add_shared('dummy_func', module)


def test_del_shared(ci):
    ci.shared_functions['foo'] = None

    ci.del_shared('foo')


def test_del_shared_unknown(ci):
    ci.del_shared('foo')


def test_has_shared(ci):
    ci.shared_functions['foo'] = None

    assert ci.has_shared('foo') is True


def test_has_shared_unknown(ci):
    assert ci.has_shared('foo') is False


def test_shared(ci):
    ci.shared_functions['foo'] = (None, MagicMock(return_value=17))

    assert ci.shared('foo', 13, 11, 'bar', arg='baz') == 17
    ci.shared_functions['foo'][1].assert_called_once_with(13, 11, 'bar', arg='baz')


def test_shared_unknown(ci):
    assert ci.shared('foo', 13, 11, 'bar', arg='baz') is None


def test_module_shared(module, monkeypatch):
    monkeypatch.setattr(module.ci, 'shared', MagicMock(return_value=17))

    assert module.shared('foo', 11, 13, 'bar', arg='baz') == 17
    module.ci.shared.assert_called_once_with('foo', 11, 13, 'bar', arg='baz')


def test_module_add_shared(module, monkeypatch):
    monkeypatch.setattr(module.ci, 'add_shared', MagicMock())
    module.shared_functions = ('foo',)

    module.add_shared()

    module.ci.add_shared.assert_called_once_with('foo', module)


def test_module_del_shared(module, monkeypatch):
    monkeypatch.setattr(module.ci, 'del_shared', MagicMock())

    module.del_shared('foo')

    module.ci.del_shared.assert_called_once_with('foo')


def test_module_has_shared(module, monkeypatch):
    monkeypatch.setattr(module.ci, 'has_shared', MagicMock(return_value=17))

    assert module.has_shared('foo') == 17
    module.ci.has_shared.assert_called_once_with('foo')
