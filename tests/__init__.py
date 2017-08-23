# pylint: disable=blacklisted-name

import bs4
import yaml

import libci


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
