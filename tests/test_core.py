# pylint: disable=blacklisted-name

import logging
import pytest

import libci

from . import NonLoadingCI


def test_run_command(log, monkeypatch):
    # pylint: disable-msg=line-too-long,too-many-statements

    import errno
    import subprocess
    from libci.utils import run_command, DEVNULL

    def assert_logging(record_count, cmd, stdout=None, stderr=None):
        records = log.records

        assert len(records) == record_count
        assert all([r.levelno == logging.DEBUG for r in records])

        assert records[0].message == cmd

        if stdout is not None:
            assert records[2].message == stdout

        if stderr is not None:
            assert records[3].message == stderr

    # Accept lists only
    log.clear()
    with pytest.raises(AssertionError, match=r'^Only list of strings accepted as a command$') as excinfo:
        run_command('/bin/ls')

    log.clear()
    with pytest.raises(AssertionError, match=r'^Only list of strings accepted as a command$'):
        run_command(['/bin/ls', 13])

    # Test some common binary
    log.clear()
    output = run_command(['/bin/ls', '/'])
    assert output.exit_code == 0
    assert 'bin' in output.stdout
    assert output.stderr == ''
    assert_logging(4, "run command: cmd='['/bin/ls', '/']', kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}",
                   stderr='stderr:\n---v---v---v---v---v---\n\n---^---^---^---^---^---')

    assert log.records[2].message.startswith('stdout:\n---v---v---v---v---v---\n')
    assert log.records[2].message.endswith('\n---^---^---^---^---^---')
    assert len(log.records[2].message.split('\n')) >= 5

    # Test non-existent binary
    log.clear()
    with pytest.raises(libci.CIError, match=r"^Command '/bin/non-existent-binary' not found$"):
        run_command(['/bin/non-existent-binary'])

    assert_logging(1, "run command: cmd='['/bin/non-existent-binary']', kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}")

    # Test existing but failing binary
    with pytest.raises(libci.CICommandError, match=r"^Command '\['/bin/false'\]' failed with exit code 1$") as excinfo:
        run_command(['/bin/false'])

    assert_logging(5, "run command: cmd='['/bin/non-existent-binary']', kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}")
    assert excinfo.value.output.exit_code == 1
    assert excinfo.value.output.stdout == ''
    assert excinfo.value.output.stderr == ''

    # Test stdout and stderr are not mixed together
    log.clear()
    cmd = ['/bin/bash', '-c', 'echo "This goes to stdout"; >&2 echo "This goes to stderr"']
    output = run_command(cmd)
    assert output.exit_code == 0
    assert output.stdout == 'This goes to stdout\n'
    assert output.stderr == 'This goes to stderr\n'
    assert_logging(4, "run command: cmd='['/bin/bash', '-c', 'echo \"This goes to stdout\"; >&2 echo \"This goes to stderr\"']', kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}",
                   stdout='stdout:\n---v---v---v---v---v---\nThis goes to stdout\n\n---^---^---^---^---^---',
                   stderr='stderr:\n---v---v---v---v---v---\nThis goes to stderr\n\n---^---^---^---^---^---')

    # Test overriding stdout and stderr
    log.clear()
    cmd = ['/bin/bash', '-c', 'echo "This goes to stdout"; >&2 echo "This goes to stderr"']
    output = run_command(cmd, stdout=DEVNULL)
    assert output.exit_code == 0
    assert output.stdout is None
    assert output.stderr == 'This goes to stderr\n'
    assert_logging(4, "run command: cmd='['/bin/bash', '-c', 'echo \"This goes to stdout\"; >&2 echo \"This goes to stderr\"']', kwargs={'stderr': 'PIPE', 'stdout': 'DEVNULL'}",
                   stdout='stdout:\n  command produced no output',
                   stderr='stderr:\n---v---v---v---v---v---\nThis goes to stderr\n\n---^---^---^---^---^---')

    log.clear()
    cmd = ['/bin/bash', '-c', 'echo "This goes to stdout"; >&2 echo "This goes to stderr"']
    output = run_command(cmd, stderr=DEVNULL)
    assert output.exit_code == 0
    assert output.stdout == 'This goes to stdout\n'
    assert output.stderr is None
    assert_logging(4, "run command: cmd='['/bin/bash', '-c', 'echo \"This goes to stdout\"; >&2 echo \"This goes to stderr\"']', kwargs={'stderr': 'DEVNULL', 'stdout': 'PIPE'}",
                   stdout='stdout:\n---v---v---v---v---v---\nThis goes to stdout\n\n---^---^---^---^---^---',
                   stderr='stderr:\n  command produced no output')

    # Test merging stdout & stderr into one
    log.clear()
    cmd = ['/bin/bash', '-c', 'echo "This goes to stdout"; >&2 echo "This goes to stderr"']
    output = run_command(cmd, stderr=subprocess.STDOUT)
    assert output.exit_code == 0
    assert output.stdout == 'This goes to stdout\nThis goes to stderr\n'
    assert output.stderr is None
    assert_logging(4, "run command: cmd='['/bin/bash', '-c', 'echo \"This goes to stdout\"; >&2 echo \"This goes to stderr\"']', kwargs={'stderr': 'STDOUT', 'stdout': 'PIPE'}",
                   stdout='stdout:\n---v---v---v---v---v---\nThis goes to stdout\nThis goes to stderr\n\n---^---^---^---^---^---',
                   stderr='stderr:\n  command produced no output')

    # Pass weird stdout value, and test its formatting in log
    stdout = (13, 17)
    log.clear()
    cmd = ['/bin/ls']
    with pytest.raises(AttributeError, match=r"^'tuple' object has no attribute 'fileno'$"):
        run_command(cmd, stdout=stdout)

    assert_logging(1, "run command: cmd='['/bin/ls']', kwargs={'stderr': 'PIPE', 'stdout': '(13, 17)'}")

    # OSError(ENOENT) raised by Popen should be translated to CIError
    def faulty_popen_enoent(*args, **kwargs):
        # pylint: disable=unused-argument
        raise OSError(errno.ENOENT, '')

    log.clear()
    monkeypatch.setattr(subprocess, 'Popen', faulty_popen_enoent)

    with pytest.raises(libci.CIError, match=r"^Command '/bin/ls' not found$"):
        run_command(['/bin/ls'])

    assert_logging(1, "run command: cmd='['/bin/ls']', kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}")
    monkeypatch.undo()

    # While other OSError instances simply pass through
    def faulty_popen_foo(*args, **kwargs):
        # pylint: disable=unused-argument
        raise OSError('foo')

    log.clear()
    monkeypatch.setattr(subprocess, 'Popen', faulty_popen_foo)

    with pytest.raises(OSError, match=r'^foo$'):
        run_command(['/bin/ls'])

    assert_logging(1, "run command: cmd='['/bin/ls']', kwargs={'stderr': 'PIPE', 'stdout': 'PIPE'}")
    monkeypatch.undo()

    # Don't capture stdout and stderr, let them pass
    log.clear()
    output = run_command(['/bin/ls', '/'], stdout=libci.utils.PARENT, stderr=libci.utils.PARENT)

    assert output.exit_code == 0
    assert output.stdout is None
    assert output.stderr is None
    assert_logging(4, "run command: cmd='['/bin/ls', '/']', kwargs={'stderr': 'PARENT', 'stdout': 'PARENT'}",
                   stdout='stdout:\n  command forwarded the output to its parent',
                   stderr='stderr:\n  command forwarded the output to its parent')


def test_check_for_commands():
    commands = ('ls', 'gzip')

    # these should exist...
    for cmd in commands:
        libci.utils.check_for_commands([cmd])

    # ... and these probably not.
    for cmd in commands:
        cmd = 'does-not-exists-' + cmd

        with pytest.raises(libci.CIError, match=r"^Command '{0}' not found on the system$".format(cmd)):
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
    with pytest.raises(Exception, match=r'^This property raised an exception$'):
        # pylint: disable=pointless-statement
        obj.bar

    assert obj.foo == 3
    assert counter['count'] == 3
    assert obj.__dict__['foo'] == 3
    assert 'bar' not in obj.__dict__


#
# Modules
#

def test_check_module_file(log, tmpdir):
    # pylint: disable=protected-access

    mfile = tmpdir.join('dummy.py')
    ci = NonLoadingCI()

    def try_pass(file_content):
        mfile.write(file_content)

        log.clear()
        assert ci._check_module_file(str(mfile)) is True
        assert log.records[0].message == "check possible module file '{}'".format(mfile)
        assert log.records[0].levelno == logging.DEBUG

    def try_fail(file_content, error):
        mfile.write(file_content)

        log.clear()
        assert ci._check_module_file(str(mfile)) is False
        assert log.records[0].message == "check possible module file '{}'".format(mfile)
        assert log.records[0].levelno == logging.DEBUG
        assert log.records[1].message == error
        assert log.records[1].levelno == logging.DEBUG

    # Test empty Python file
    try_fail('pass', "  no 'import libci' found")

    # Check file that imports libci but does not have module class
    try_fail('import libci', "  no child of libci.Module found")
    try_fail('from libci import CI', "  no child of libci.Module found")

    # Check we ignore module classes with wrong base class
    try_fail("""
import libci

class DummyModule(object):
    pass
""", "  no child of libci.Module found")

    # Check file that does have module class, but that does not import libci
    try_fail("""
class DummyModule(libci.Module):
    pass
""", "  no 'import libci' found")

    # Check file that both imports libci, and has module class
    try_pass("""
import libci

class DummyModule(libci.Module):
    pass
""")

    try_pass("""
from libci import Module

class DummyModule(Module):
    pass
""")


class DummyModule(libci.Module):
    """
    Very dummy module, implementing necessary methods and attributes
    to pass through CI's internal machinery.
    """

    name = 'Dummy module'


def test_module_instantiate():
    """
    Try to instantiate a module, and check some of its properties.
    """

    ci = NonLoadingCI()
    mod = DummyModule(ci)

    assert mod.ci == ci

    # pylint: disable=protected-access,no-member
    assert mod.debug == mod.logger.debug
    assert mod.verbose == mod.logger.verbose
    assert mod.info == mod.logger.info
    assert mod.warn == mod.logger.warning
    assert mod.error == mod.logger.error
    assert mod.exception == mod.logger.exception

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

    with pytest.raises(libci.CIError, match=r"^No such shared function 'baz' of module 'Dummy module'$"):
        mod.add_shared()

    assert ci.shared_functions == {}


def test_module_del_shared():
    """
    Excercise unregistering shared functions.
    """

    ci = NonLoadingCI()

    class UsefulModule(DummyModule):
        shared_functions = ['foo', 'bar']

        def foo(self):
            pass

        def bar(self):
            pass

    mod = UsefulModule(ci)

    mod.add_shared()
    assert sorted(ci.shared_functions.keys()) == ['bar', 'foo']

    # Remove shared function
    mod.del_shared('foo')
    assert 'foo' not in ci.shared_functions

    ci.del_shared('bar')
    assert ci.shared_functions == {}

    # Try removing unknown shared function
    # foo is now already removed, right?
    mod.del_shared('foo')
    assert ci.shared_functions == {}

    ci.del_shared('foo')
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

    # call to unknown shared function returns nothing
    assert ci.shared('bar', 'a', 13, baz='1335') is None
    assert mod.ci.shared('bar', 'a', 13, baz='1335') is None
