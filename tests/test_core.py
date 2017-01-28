# pylint: disable=blacklisted-name

import logging
import pytest

import libci


def test_import_sanity():
    libci.CI()


def test_run_command(monkeypatch, caplog):
    # pylint: disable-msg=line-too-long,too-many-statements

    import errno
    import subprocess
    from libci.utils import run_command, DEVNULL

    def caplog_clear():
        caplog.handler.records = []

    def assert_logging(record_count, cmd, stdout=None, stderr=None):
        assert len(caplog.records) == record_count
        assert all([r.levelno == logging.DEBUG for r in caplog.records])

        assert caplog.records[0].message == cmd

        if stdout is not None:
            assert caplog.records[1].message == stdout

        if stderr is not None:
            assert caplog.records[2].message == stderr

    # Accept lists only
    caplog_clear()
    with pytest.raises(AssertionError, message='Only list of strings accepted as a command'):
        run_command('/bin/ls')

    with pytest.raises(AssertionError, message='Only list of strings accepted as a command'):
        run_command(['/bin/ls', 13])

    # Test some common binary
    caplog_clear()
    output = run_command(['/bin/ls', '/'])
    assert output.exit_code == 0
    assert 'bin' in output.stdout
    assert output.stderr == ''
    assert_logging(3, "run command: cmd='['/bin/ls', '/']', args=(), kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}",
                   stderr='stderr:\n------------------\n\n------------------')

    assert caplog.records[1].message.startswith('stdout:\n------------------\n')
    assert caplog.records[1].message.endswith('\n------------------')
    assert len(caplog.records[1].message.split('\n')) >= 5

    # Test non-existent binary
    caplog_clear()
    with pytest.raises(libci.CIError, message="Command '/bin/non-existent-binary' not found"):
        run_command(['/bin/non-existent-binary'])

    assert_logging(1, "run command: cmd='['/bin/non-existent-binary']', args=(), kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}")

    # Test existing but failing binary
    with pytest.raises(libci.CICommandError, message="Command '/bin/false' failed with exit code 1") as excinfo:
        run_command(['/bin/false'])

    assert_logging(4, "run command: cmd='['/bin/non-existent-binary']', args=(), kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}")
    assert excinfo.value.output.exit_code == 1
    assert excinfo.value.output.stdout == ''
    assert excinfo.value.output.stderr == ''

    # Test stdout and stderr are not mixed together
    caplog_clear()
    cmd = ['/bin/bash', '-c', 'echo "This goes to stdout"; >&2 echo "This goes to stderr"']
    output = run_command(cmd)
    assert output.exit_code == 0
    assert output.stdout == 'This goes to stdout\n'
    assert output.stderr == 'This goes to stderr\n'
    assert_logging(3, "run command: cmd='['/bin/bash', '-c', 'echo \"This goes to stdout\"; >&2 echo \"This goes to stderr\"']', args=(), kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}",
                   stdout='stdout:\n------------------\nThis goes to stdout\n\n------------------',
                   stderr='stderr:\n------------------\nThis goes to stderr\n\n------------------')

    # Test overriding stdout and stderr
    caplog_clear()
    cmd = ['/bin/bash', '-c', 'echo "This goes to stdout"; >&2 echo "This goes to stderr"']
    output = run_command(cmd, stdout=DEVNULL)
    assert output.exit_code == 0
    assert output.stdout is None
    assert output.stderr == 'This goes to stderr\n'
    assert_logging(3, "run command: cmd='['/bin/bash', '-c', 'echo \"This goes to stdout\"; >&2 echo \"This goes to stderr\"']', args=(), kwargs={'stderr': 'PIPE', 'stdout': 'DEVNULL'}",
                   stdout='  command produced no output on stdout',
                   stderr='stderr:\n------------------\nThis goes to stderr\n\n------------------')

    caplog_clear()
    cmd = ['/bin/bash', '-c', 'echo "This goes to stdout"; >&2 echo "This goes to stderr"']
    output = run_command(cmd, stderr=DEVNULL)
    assert output.exit_code == 0
    assert output.stdout == 'This goes to stdout\n'
    assert output.stderr is None
    assert_logging(3, "run command: cmd='['/bin/bash', '-c', 'echo \"This goes to stdout\"; >&2 echo \"This goes to stderr\"']', args=(), kwargs={'stderr': 'DEVNULL', 'stdout': 'PIPE'}",
                   stdout='stdout:\n------------------\nThis goes to stdout\n\n------------------',
                   stderr='  command produced no output on stderr')

    # Test merging stdout & stderr into one
    caplog_clear()
    cmd = ['/bin/bash', '-c', 'echo "This goes to stdout"; >&2 echo "This goes to stderr"']
    output = run_command(cmd, stderr=subprocess.STDOUT)
    assert output.exit_code == 0
    assert output.stdout == 'This goes to stdout\nThis goes to stderr\n'
    assert output.stderr is None
    assert_logging(3, "run command: cmd='['/bin/bash', '-c', 'echo \"This goes to stdout\"; >&2 echo \"This goes to stderr\"']', args=(), kwargs={'stderr': 'STDOUT', 'stdout': 'PIPE'}",
                   stdout='stdout:\n------------------\nThis goes to stdout\nThis goes to stderr\n\n------------------',
                   stderr='  command produced no output on stderr')

    # Pass weird stdout value, and test its formatting in log
    stdout = (13, 17)
    caplog_clear()
    cmd = ['/bin/ls']
    with pytest.raises(AttributeError, message="'tuple' object has no attribute 'fileno'"):
        run_command(cmd, stdout=stdout)

    assert_logging(1, "run command: cmd='['/bin/ls']', args=(), kwargs={'stderr': 'PIPE', 'stdout': (13, 17)}")

    # OSError(ENOENT) raised by Popen should be translated to CIError
    def faulty_popen_enoent(*args, **kwargs):
        # pylint: disable=unused-argument
        raise OSError(errno.ENOENT, '')

    caplog_clear()
    monkeypatch.setattr(subprocess, 'Popen', faulty_popen_enoent)

    with pytest.raises(libci.CIError, message="Command '/bin/ls' not found"):
        run_command(['/bin/ls'])

    assert_logging(1, "run command: cmd='['/bin/ls']', args=(), kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}")
    monkeypatch.undo()

    # While other OSError instances simply pass through
    def faulty_popen_foo(*args, **kwargs):
        # pylint: disable=unused-argument
        raise OSError('foo')

    caplog_clear()
    monkeypatch.setattr(subprocess, 'Popen', faulty_popen_foo)

    with pytest.raises(OSError, message='foo'):
        run_command(['/bin/ls'])

    assert_logging(1, "run command: cmd='['/bin/ls']', args=(), kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}")
    monkeypatch.undo()


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


def test_cached_property():
    from libci.utils import cached_property

    counter = {
        'count': 0
    }

    class DummyClass(object):
        # pylint: disable=too-few-public-methods
        @cached_property
        def foo(self):
            # pylint: disable=no-self-use
            counter['count'] += 1
            return counter['count']

        @cached_property
        def bar(self):
            # pylint: disable=no-self-use
            raise Exception('This property raised an exception')

    obj = DummyClass()
    assert counter['count'] == 0
    assert 'foo' not in obj.__dict__
    assert 'bar' not in obj.__dict__

    # first access should increase the counter
    assert 'bar' not in obj.__dict__
    assert obj.foo == 1
    assert counter['count'] == 1
    assert obj.__dict__['foo'] == 1

    # the second access should return cached value
    assert 'bar' not in obj.__dict__
    assert obj.foo == 1
    assert counter['count'] == 1
    assert obj.__dict__['foo'] == 1

    # increase counter, and observe property
    counter['count'] += 1
    assert 'bar' not in obj.__dict__
    assert obj.foo == 1
    assert obj.__dict__['foo'] == 1

    # remove attribute, and try again - this should clear the cache
    del obj.foo
    assert 'bar' not in obj.__dict__
    assert obj.foo == 3
    assert counter['count'] == 3
    assert obj.__dict__['foo'] == 3

    # when exception is raised, there should be no changes in __dict__
    with pytest.raises(Exception, message='This property raised an exception'):
        # pylint: disable=pointless-statement
        obj.bar

    assert obj.foo == 3
    assert counter['count'] == 3
    assert obj.__dict__['foo'] == 3
    assert 'bar' not in obj.__dict__


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

    ci = NonLoadingCI()
    mod = DummyModule(ci)

    assert mod.ci == ci

    assert mod.debug == libci.Logging.get_logger().debug
    assert mod.verbose == libci.Logging.get_logger().verbose
    assert mod.info == libci.Logging.get_logger().info
    assert mod.warn == libci.Logging.get_logger().warn
    assert mod.error == libci.Logging.get_logger().error
    assert mod.exception == libci.Logging.get_logger().exception

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
