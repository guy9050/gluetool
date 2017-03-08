"""
Various helpers.
"""

import errno
import json
import os
import subprocess

from libci import CIError, CICommandError
from libci.log import Logging


try:
    from subprocess import DEVNULL
except ImportError:
    DEVNULL = open(os.devnull, 'wb')

# Use this constant to order run_command to pass child's output stream
# to its parent's corresponding stream.
#
# I don't want this to collide with any subprocess constant, or possible filename
PARENT = (17,)


def log_blob(logger, intro, blob):
    logger("{}:\n------------------\n{}\n------------------".format(intro, blob))


class ProcessOutput(object):
    """
    Result of external process.
    """

    # pylint: disable=too-many-arguments,too-few-public-methods
    def __init__(self, cmd, exit_code, stdout, stderr, kwargs):
        self.cmd = cmd
        self.kwargs = kwargs

        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    def log_stream(self, stream, logger):
        content = getattr(self, stream)

        if content is None:
            if stream in self.kwargs:
                logger('{}:\n  command produced no output'.format(stream))
            else:
                logger('{}:\n  command forwarded the output to its parent'.format(stream))

        else:
            log_blob(logger, stream, content)


def run_command(cmd, logger=None, **kwargs):
    """
    Run external command, and return it's exit code and output.

    This is a very thin and simple wrapper above `subprocess.Popen`, and its
    main purpose is to log everything that happens before and after execution.
    All additional arguments are passed directly to `Popen` constructor.

    If `stdout` or `stderr` keyword arguments are not specified, function
    will set them to `subprocess.PIPE`, to capture both output streams in
    separate strings.

    :param list cmd: command to execute.
    :rtype: ProcessOutput instance
    :returns: `ProcessOutput` instance whose attributes contain data returned
      by the process.
    :raises CIError: when command was not found.
    :raises CICommandError: when command exited with non-zero exit code.
    :raises Exception: when anything else breaks.
    """

    assert isinstance(cmd, list), 'Only list of strings accepted as a command'
    assert all((isinstance(s, str) for s in cmd)), 'Only list of strings accepted as a command'

    logger = logger or Logging.get_logger()

    stdout, stderr = None, None

    # Set default stdout/stderr, unless told otherwise
    if 'stdout' not in kwargs:
        kwargs['stdout'] = subprocess.PIPE

    if 'stderr' not in kwargs:
        kwargs['stderr'] = subprocess.PIPE

    def _format_stream(stream):
        if stream == subprocess.PIPE:
            return 'PIPE'
        if stream == DEVNULL:
            return 'DEVNULL'
        if stream == subprocess.STDOUT:
            return 'STDOUT'
        if stream == PARENT:
            return 'PARENT'
        return stream

    printable_kwargs = kwargs.copy()
    for stream in ('stdout', 'stderr'):
        if stream in printable_kwargs:
            printable_kwargs[stream] = _format_stream(printable_kwargs[stream])

    if kwargs['stdout'] == PARENT:
        del kwargs['stdout']

    if kwargs['stderr'] == PARENT:
        del kwargs['stderr']

    # Make tests happy by sorting kwargs - it's a dictionary, therefore
    # unpredictable from the observer's point of view. Can print its entries
    # in different order with different Pythons, making tests a mess.
    sorted_kwargs = ', '.join(["'%s': '%s'" % (k, printable_kwargs[k]) for k in sorted(printable_kwargs.iterkeys())])

    logger.debug("run command: cmd='%s', kwargs={%s}" % (cmd, sorted_kwargs))

    try:
        p = subprocess.Popen(cmd, **kwargs)

    except OSError as e:
        if e.errno == errno.ENOENT:
            raise CIError("Command '{}' not found".format(cmd[0]))

        raise e

    stdout, stderr = p.communicate()
    exit_code = p.poll()

    output = ProcessOutput(cmd, exit_code, stdout, stderr, kwargs)

    output.log_stream('stdout', logger.debug)
    output.log_stream('stderr', logger.debug)

    if exit_code != 0:
        raise CICommandError(cmd, output)

    return output


def check_for_commands(cmds):
    """ Checks if all commands in list cmds are valid """
    for cmd in cmds:
        try:
            run_command(['/bin/bash', '-c', 'command -v {}'.format(cmd)], stdout=DEVNULL)

        except CIError:
            raise CIError("Command '{}' not found on the system".format(cmd))


class cached_property(object):
    # pylint: disable=invalid-name,too-few-public-methods
    """
    `property`-like decorator - at first access, it calls decorated
    method to acquire the real value, and then replaces itself with
    this value, making it effectively "cached". Useful for properties
    whose value does not change over time, and where getting the real
    value could penalize execution with unnecessary (network, memory)
    overhead.

    Delete attribute to clear the cached value - on next access, decorated
    method will be called again, to acquire the real value.

    Of possible options, only read-only instance attribute access is
    supported so far.
    """

    def __init__(self, method):
        self._method = method
        self.__doc__ = getattr(method, '__doc__')

    def __get__(self, obj, cls):
        # does not support class attribute access, only instance
        assert obj is not None

        # get the real value of this property
        value = self._method(obj)

        # replace cached_property instance with the value
        obj.__dict__[self._method.__name__] = value

        return value


def format_command_line(cmdline):
    """
    Return formatted command-line.

    All but the first line are indented by 4 spaces.

    :param cmdline: list of iterables, representing command-line split to multiple lines.
    """

    def _format_options(options):
        return ' '.join(['"%s"' % opt for opt in options])

    cmd = [_format_options(cmdline[0])]

    for row in cmdline[1:]:
        cmd.append('    ' + _format_options(row))

    return ' \\\n'.join(cmd)


def format_dict(dictionary):
    # Use custom "default" handler, to at least encode obj's repr() output when
    # json encoder does not know how to encode such class
    def default(obj):
        return repr(obj)

    return json.dumps(dictionary, sort_keys=True, indent=4, separators=(',', ': '), default=default)
