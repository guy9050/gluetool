# pylint: disable=blacklisted-name

import yaml

import libci


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

    def clear(self):
        """
        Clear list of captured records.
        """

        self._caplog.handler.records = []


def create_module(module_class, ci_class=NonLoadingCI):
    ci = ci_class()
    mod = module_class(ci)
    mod.add_shared()

    return ci, mod


def create_yaml(tmpdir, name, data):
    f = tmpdir.join(name)
    f.write(yaml.dump(data))
    return f
