# pylint: disable=blacklisted-name

import pytest

import libci


def test_import_sanity():
    libci.CI()


def test_check_for_commands():
    commands = ('ls', 'gzip')

    # these should exist...
    for cmd in commands:
        libci.utils.check_for_commands([cmd])

    # ... and these probably not.
    for cmd in commands:
        cmd = 'does-not-exists-' + cmd

        with pytest.raises(libci.CIError, message='\'{0}\' command not found on the system'.format(cmd)):
            libci.utils.check_for_commands([cmd])


#
# Modules
#
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


class DummyModule(libci.Module):
    """
    Very dummy module, implementing necessary methods and attributes
    to pass through CI's internal machinery.
    """

    name = 'Dummy module'

    def execute(self):
        pass


def test_module_instantiate():
    """
    Try to instantiate a module, and check some of its properties.
    """

    import functools

    ci = NonLoadingCI()
    mod = DummyModule(ci)

    assert mod.ci == ci

    def assert_logging_helper(name, level=None):
        helper = getattr(mod, name)

        assert isinstance(helper, functools.partial)
        assert helper.func == mod.log
        assert helper.args == ()

        if level is None:
            assert not helper.keywords
        else:
            assert 'level' in helper.keywords
            assert helper.keywords['level'] == level

    assert_logging_helper('debug', level='D')
    assert_logging_helper('verbose', level='V')
    assert_logging_helper('info')
    assert_logging_helper('warn', level='W')

    assert mod.config_parser is None
    # pylint: disable-msg=protected-access
    assert not mod._config

    assert mod.data_path is None  # There's no data path for our "Dummy module"


def test_module_add_shared():
    """
    Excercise registering shared functions.
    """

    ci = NonLoadingCI()

    class UsefulModule(DummyModule):
        def foo(self):
            pass

        def bar(self):
            pass

    mod = UsefulModule(ci)

    # First, mod has no shared functions, so no should appear in parent's registry
    assert mod.shared_functions == []
    ci.shared_functions = {}

    mod.add_shared()
    assert ci.shared_functions == {}

    # Second, add some shared functions, and try again
    mod.shared_functions = ['foo', 'bar']
    ci.shared_functions = {}

    mod.add_shared()

    assert sorted(ci.shared_functions.keys()) == ['bar', 'foo']
    assert ci.shared_functions['foo'] == (mod, mod.foo)
    assert ci.shared_functions['bar'] == (mod, mod.bar)

    # Try also some non-existent functions
    mod.shared_functions = ['baz']
    ci.shared_functions = {}

    with pytest.raises(libci.CIError, message="No such shared function 'baz' of module 'Dummy module'"):
        mod.add_shared()

    assert ci.shared_functions == {}


def test_module_shared():
    """
    Call shared functions.
    """

    ci = NonLoadingCI()

    class UsefulModule(DummyModule):
        shared_functions = ['foo']

        def foo(self, *args, **kwargs):
            # pylint: disable-msg=no-self-use
            return 'foo: {}, {}'.format(args, kwargs)

    mod = UsefulModule(ci)
    mod.add_shared()

    # call shared function with some arguments, like other modules would do
    assert ci.shared('foo', 'a', 13, baz='1335') == "foo: ('a', 13), {'baz': '1335'}"

    # it should produce the same result when called directly
    assert mod.shared('foo', 'a', 13, baz='1335') == "foo: ('a', 13), {'baz': '1335'}"
