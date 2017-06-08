# pylint: disable=blacklisted-name

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

    def _load_config(self):
        pass


def create_module(module_class, ci_class=NonLoadingCI):
    ci = ci_class()
    mod = module_class(ci)
    mod.add_shared()

    return ci, mod
