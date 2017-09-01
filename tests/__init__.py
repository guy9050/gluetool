# pylint: disable=blacklisted-name

import bs4
import yaml

import pytest

import libci

from mock import MagicMock


class Bunch(object):
    # pylint: disable=too-few-public-methods

    """
    Object-like access to a dictionary - useful for many mock objects.
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class NonLoadingCI(libci.CI):
    """
    Current CI implementation loads modules and configs when instantiated,
    which makes it *really* hard to make assumptions of the state of its
    internals - they will always be spoiled by other modules, other external
    resources the tests cannot control. So, to overcome this I use this
    custom CI class that disables loading of modules and configs on its
    instantiation.

    See https://gitlab.cee.redhat.com/mvadkert/citool/issues/15.
    """

    def _load_modules(self):
        pass

    def parse_config(self, *args, **kwargs):
        # pylint: disable=arguments-differ

        pass

    def parse_args(self, *args, **kwargs):
        # pylint: disable=arguments-differ

        pass


class CaplogWrapper(object):
    """
    Thin wrapper around pytest's caplog plugin.
    """

    def __init__(self, caplog):
        self._caplog = caplog

    @property
    def records(self):
        return self._caplog.records

    def __repr__(self):
        return '\n'.join(["<Record: msg='{}'>".format(record.message) for record in self.records])

    def clear(self):
        """
        Clear list of captured records.
        """

        self._caplog.handler.records = []

    def match(self, matcher=any, **kwargs):
        def _cmp(record):
            return all(getattr(record, field) == value for field, value in kwargs.iteritems())

        return matcher(_cmp(record) for record in self.records)


def assert_shared(name, func, *args, **kwargs):
    """
    Syntax sugar for ``pytest.raises`` when testing whether called code checks for shared function.

    :param str name: name of shared function the test expect to be missing.
    :param callable func: Callable piece that should raise an exception.
    :param args: Arguments for ``func``.
    :param kwargs: Keyword arguments for ``func``.
    """

    pattern = r"^Shared function '{}' is required. See `citool -L` to find out which module provides it.$".format(name)

    with pytest.raises(libci.CIError, match=pattern):
        func(*args, **kwargs)


def patch_shared(monkeypatch, module, shared_functions):
    """
    Monkeypatch registry of shared functions. This helper is intended for simple and common use cases
    where test needs to inject its own list of functions that return values. If you need anything
    more complicated, you're on your own.

    :param monkeypatch: Monkeypatch fixture, usually passed to the original test function.
    :param module: Module instance that serves as an access point to CI internals.
    :param dict(str, obj) shared_functions: Maping between names and return values.
    """

    monkeypatch.setattr(module.ci, 'shared_functions', {
        name: (None, MagicMock(return_value=value)) for name, value in shared_functions.iteritems()
    })


def create_module(module_class, ci_class=NonLoadingCI, name='dummy-module'):
    ci = ci_class()
    mod = module_class(ci, name)
    mod.add_shared()

    return ci, mod


def create_yaml(tmpdir, name, data):
    f = tmpdir.join(name)
    f.write(yaml.dump(data))
    return f


def xml(text):
    return bs4.BeautifulSoup(text, 'xml').contents[0]
