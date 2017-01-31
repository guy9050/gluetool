"""
Various helpers.
"""

import collections
import errno
import subprocess

from libci import CIError, CICommandError
from libci.log import Logging


try:
    from subprocess import DEVNULL
except ImportError:
    import os
    DEVNULL = open(os.devnull, 'wb')


#: Result of external process.
ProcessOutput = collections.namedtuple('ProcessOutput', ['exit_code', 'stdout', 'stderr'])


def run_command(cmd, *args, **kwargs):
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

    log = Logging.get_logger()

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
        return stream

    printable_kwargs = kwargs.copy()
    for stream in ('stdout', 'stderr'):
        if stream in printable_kwargs:
            printable_kwargs[stream] = _format_stream(printable_kwargs[stream])

    log.debug("run command: cmd='{}', args={}, kwargs={}".format(cmd, args, printable_kwargs))

    stdout, stderr = None, None

    try:
        p = subprocess.Popen(cmd, *args, **kwargs)

    except OSError as e:
        if e.errno == errno.ENOENT:
            raise CIError("Command '{}' not found".format(cmd[0]))

        raise e

    stdout, stderr = p.communicate()
    exit_code = p.poll()

    def log_standard_stream(name, content):
        if content is None:
            log.debug('  command produced no output on {}'.format(name))
        else:
            log.debug("{}:\n------------------\n{}\n------------------".format(name, content))

    log_standard_stream('stdout', stdout)
    log_standard_stream('stderr', stderr)

    output = ProcessOutput(exit_code, stdout, stderr)

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
