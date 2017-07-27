"""
Various helpers.
"""

import collections
import errno
import os
import re
import subprocess
import sys
import threading
import time
import urllib2
import yaml

import urlnorm
import mako

from libci import CIError, CICommandError
from libci.log import Logging, ContextAdapter, log_blob, BlobLogger, format_dict


try:
    # pylint: disable=ungrouped-imports
    from subprocess import DEVNULL
except ImportError:
    DEVNULL = open(os.devnull, 'wb')


def dict_update(dst, *args):
    """
    Python's ``dict.update`` does not return the dictionary just updated but a ``None``. This function
    is a helper that does updates the dictionary *and* returns it. So, instead of:

    .. code-block:: python

       d.update(other)
       return d

    you can use:

    .. code-block:: python

       return dict_update(d, other)

    :param dict dst: dictionary to be updated.
    :param args: dictionaries to update ``dst`` with.
    """

    for other in args:
        assert isinstance(other, dict)

        dst.update(other)

    return dst


class Bunch(object):
    # pylint: disable=too-few-public-methods

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class ThreadAdapter(ContextAdapter):
    """
    Custom logger adapter, adding thread name as a context.

    :param libci.log.ContextAdapter logger: parent logger whose methods will be used for logging.
    :param threading.Thread thread: thread whose name will be added.
    """

    def __init__(self, logger, thread):
        super(ThreadAdapter, self).__init__(logger, {'ctx_thread_name': (5, thread.name)})


class WorkerThread(threading.Thread):
    """
    Worker threads gets a job to do, and returns a result. It gets a callable, ``fn``,
    which will be called in thread's ``run()`` method, and thread's ``result`` property
    will be the result - value returned by ``fn``, or exception raised during the
    runtime of ``fn``.

    :param libci.log.ContextAdapter logger: logger to use for logging.
    :param fn: thread will start `fn` to do the job.
    :param fn_args: arguments for `fn`
    :param fn_kwargs: keyword arguments for `fn`
    """

    def __init__(self, logger, fn, fn_args=None, fn_kwargs=None, *args, **kwargs):
        threading.Thread.__init__(self, *args, **kwargs)

        self.logger = ThreadAdapter(logger, self)
        self.logger.connect(self)

        self._fn = fn
        self._args = fn_args or ()
        self._kwargs = fn_kwargs or {}

        self.result = None

    def run(self):
        self.debug('worker thread started')

        try:
            self.result = self._fn(*self._args, **self._kwargs)

        # pylint: disable=broad-except
        except Exception as e:
            self.exception('exception raised in worker thread: {}'.format(str(e)))
            self.result = e

        finally:
            self.debug('worker thread finished')


class StreamReader(object):
    def __init__(self, stream, block=16):
        """
        Wrap blocking ``stream`` with a reading thread. The threads read from
        the (normal, blocking) `stream` and adds bits and pieces into the `queue`.
        ``StreamReader`` user then can check the `queue` for new data.
        """

        self._stream = stream

        # List would fine as well, however deque is better optimized for
        # FIFO operations, and it provides the same thread safety.
        self._queue = collections.deque()
        self._content = []

        def _enqueue():
            """
            Read what's available in stream and add it into the queue
            """

            while True:
                data = self._stream.read(block)

                if not data:
                    # signal EOF
                    self._queue.append('')
                    return

                self._queue.append(data)
                self._content.append(data)

        self._thread = threading.Thread(target=_enqueue)
        self._thread.daemon = True
        self._thread.start()

    @property
    def name(self):
        return self._stream.name

    @property
    def content(self):
        return ''.join(self._content)

    def wait(self):
        self._thread.join()

    def read(self):
        try:
            return self._queue.popleft()

        except IndexError:
            return None


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


def run_command(cmd, logger=None, inspect=False, inspect_callback=None, **kwargs):
    """
    Run external command, and return it's exit code and output.

    This is a very thin and simple wrapper above :py:class:`subprocess.Popen`,
    and its main purpose is to log everything that happens before and after
    execution. All additional arguments are passed directly to `Popen` constructor.

    If ``stdout`` or ``stderr`` keyword arguments are not specified, function
    will set them to :py:const:`subprocess.PIPE`, to capture both output streams
    in separate strings.

    By default, output of the process is captured for both ``stdout`` and ``stderr``,
    and returned back to the caller. Under some conditions, caller might want to see
    the output in "real-time". For that purpose, it can pass callable via ``inspect``
    parameter - such callable will be called for every received bit of input on both
    ``stdout`` and ``stderr``. E.g.

    .. code-block:: python

       def foo(stream, s):
         if s is not None and 'a' in s:
           print s

       run_command(['/bin/foo'], inspect=foo)

    This example will print all substrings containing letter `a`. Strings passed to ``foo``
    may be of arbitrary lengths, and may change between subsequent calls of ``run_command``.

    :param list cmd: command to execute.
    :param libci.log.ContextAdapter logger: parent logger whose methods will be used for logging.
    :param bool inspect: if set, ``inspect_callback`` will receive the output of command in "real-time".
    :param callable inspect_callback: callable that will receive command output. If not set,
        default "write to ``sys.stdout``" is used.
    :rtype: libci.utils.ProcessOutput instance
    :returns: :py:class:`libci.utils.ProcessOutput` instance whose attributes contain
        data returned by the process.
    :raises libci.ci.CIError: when command was not found.
    :raises libci.ci.CICommandError: when command exited with non-zero exit code.
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
        return stream

    printable_kwargs = kwargs.copy()
    for stream in ('stdout', 'stderr'):
        if stream in printable_kwargs:
            printable_kwargs[stream] = _format_stream(printable_kwargs[stream])

    # Make tests happy by sorting kwargs - it's a dictionary, therefore
    # unpredictable from the observer's point of view. Can print its entries
    # in different order with different Pythons, making tests a mess.
    sorted_kwargs = ', '.join(["'%s': '%s'" % (k, printable_kwargs[k]) for k in sorted(printable_kwargs.iterkeys())])

    logger.debug("run command: cmd='{}', kwargs={{{}}}".format(cmd, sorted_kwargs))

    try:
        p = subprocess.Popen(cmd, **kwargs)

        if inspect is True:
            # let's capture *both* streams - capturing just a single one leads to so many ifs
            # and elses and messy code
            p_stdout = StreamReader(p.stdout)
            p_stderr = StreamReader(p.stderr)

            if inspect_callback is None:
                def stdout_write(stream, data):
                    # pylint: disable=unused-argument

                    if data is None:
                        return

                    # Not suitable for multiple simultaneous commands. Shuffled output will
                    # ruin your day. And night. And few following weeks, full of debugging, as well.
                    sys.stdout.write(data)
                    sys.stdout.flush()

                inspect_callback = stdout_write

            inputs = (p_stdout, p_stderr)

            with BlobLogger('Output of command: {}'.format(format_command_line([cmd])), outro='End of command output',
                            writer=logger.info):
                logger.debug("output of command is inspected by the caller")
                logger.debug('following blob-like header and footer are expected to be empty')
                logger.debug('the captured output will follow them')

                # As long as process runs, keep calling callbacks with incoming data
                while True:
                    for stream in inputs:
                        inspect_callback(stream, stream.read())

                    if p.poll() is not None:
                        break

                    # give up OS' attention and let others run
                    time.sleep(0.1)

                # OK, process finished but we have to wait for our readers to finish as well
                p_stdout.wait()
                p_stderr.wait()

                for stream in inputs:
                    while True:
                        data = stream.read()

                        if data in ('', None):
                            break

                        inspect_callback(stream, data)

            stdout, stderr = p_stdout.content, p_stderr.content

        else:
            stdout, stderr = p.communicate()

    except OSError as e:
        if e.errno == errno.ENOENT:
            raise CIError("Command '{}' not found".format(cmd[0]))

        raise e

    exit_code = p.poll()

    output = ProcessOutput(cmd, exit_code, stdout, stderr, kwargs)

    logger.debug('command exited with code {}'.format(output.exit_code))
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
    ``property``-like decorator - at first access, it calls decorated
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

    :param list cmdline: list of iterables, representing command-line split to multiple lines.
    """

    def _format_options(options):
        return ' '.join(['"%s"' % opt for opt in options])

    cmd = [_format_options(cmdline[0])]

    for row in cmdline[1:]:
        cmd.append('    ' + _format_options(row))

    return ' \\\n'.join(cmd)


def fetch_url(url, logger=None, success_codes=(200,)):
    """
    "Get me content of this URL" helper.

    Very thin wrapper around urllib. Added value is logging, and converting
    possible errors to :py:class:`libci.ci.CIError` exception.

    :param str url: URL to get.
    :param libci.log.ContextLogger logger: Logger used for logging.
    :param tuple success_codes: tuple of HTTP response codes representing successfull request.
    :returns: tuple ``(response, content)`` where ``response`` is what :py:func:`urllib2.urlopen`
      returns, and ``content`` is the payload of the response.
    """

    logger = logger or Logging.get_logger()

    logger.debug("opening URL '{}'".format(url))

    try:
        response = urllib2.urlopen(url)
        code, content = response.getcode(), response.read()

    except urllib2.HTTPError as exc:
        raise CIError("Failed to fetch URL '{}': {}".format(url, exc.message))

    log_blob(logger.debug, '{}: {}'.format(url, code), content)

    if code not in success_codes:
        raise CIError("Unsuccessfull response from '{}'".format(url))

    return response, content


def treat_url(url, shorten=False, logger=None):
    """
    Remove "weird" artifacts from the given URL. Collapse adjacent '.'s, apply '..', etc.

    :param str url: URL to clear.
    :param bool shorten: If ``True``, will try to shorten the URL, using remote service.
    :param libci.log.ContextAdapter logger: parent logger whose methods will be used for logging.
      This is purely optional, used only when contacting shortening service.
    :rtype: str
    :returns: Treated URL.
    """

    try:
        url = str(urlnorm.norm(url))

    except urlnorm.InvalidUrl as exc:
        # urlnorm cannot handle localhost: https://github.com/jehiah/urlnorm/issues/3
        if exc.message == "host u'localhost' is not valid":
            pass

        else:
            raise exc

    if shorten is True:
        try:
            _, url = fetch_url('https://url.corp.redhat.com/new?{}'.format(url), logger=logger)

        except CIError as exc:
            logger.warn('Unable to shorten URL (see log for more details): {}'.format(exc.message), sentry=True)

    return url.strip()


def render_template(tmpl, **kwargs):
    """
    Render template. Logs errors, and raises an exception when it's not possible
    to correctly render the remplate.

    :param tmpl: Template to render. It can be either :py:class:`mako.template.Template` instance,
      or a string.
    :param dict kwargs: Keyword arguments passed to render process.
    :rtype: str
    :returns: Rendered template.
    :raises libci.ci.CIError: when the rednering failed.
    """

    try:
        if isinstance(tmpl, str):
            return tmpl.format(**kwargs).strip()

        if isinstance(tmpl, mako.template.Template):
            return tmpl.render(**kwargs).strip()

        raise CIError('Unhandled template type {}'.format(type(tmpl)))

    except:
        details = mako.exceptions.text_error_template().render()
        raise CIError('Cannot render template:\n{}'.format(details))


def load_yaml(filepath, logger=None):
    """
    Load data stored in YAML file, and return their Python representation.

    :param str filepath: Path to a file. ``~`` or ``~<username>`` are expanded before using.
    :param libci.log.ContextLogger logger: Logger used for logging.
    :rtype: object
    :returns: structures representing data in the file.
    :raises libci.ci.CIError: if it was not possible to successfully load content of the file.
    """

    if not filepath:
        raise CIError('File path is not valid: {}'.format(filepath))

    logger = logger or Logging.get_logger()

    real_filepath = os.path.expanduser(filepath)

    if not os.path.exists(real_filepath):
        raise CIError("File '{}' does not exist".format(filepath))

    try:
        with open(real_filepath, 'r') as f:
            data = yaml.load(f)
            logger.debug("loaded YAML data from '{}':\n{}".format(filepath, format_dict(data)))

            return data

    except yaml.YAMLError as e:
        raise CIError("Unable to load YAML file '{}': {}".format(filepath, str(e)))


class PatternMap(object):
    # pylint: disable=too-few-public-methods

    """
    `Pattern map` is a list of ``<pattern>``: ``<converter>`` pairs. ``Pattern`` is a
    regular expression used to match a string, ``converter`` is a function that transforms
    a string into another one, accepting the pattern and the string as arguments.

    It is defined in a YAML file:

    .. code-block:: yaml

       ---
       - 'foo-(\\d+)': 'bar-\\1'
       - 'baz-(\\d+)': 'baz, find_the_most_recent, append_dot'

    Patterns are the keys in each pair, while ``converter`` is a string consisting of multiple
    items, separated by comma. The first item is **always** a string, let's call it ``R``.
    ``R``, given some string ``S1`` and the pattern, is used to transform ``S1`` to a new string,
    ``S2``, by calling ``pattern.sub(R, S1)``. ``R`` can make use of anything :py:meth:`re.sub`
    supports, including capturing groups.

    If there are other items in the ``converter`` string, they are names of `spices`, additional
    functions that will be called with ``pattern`` and the output of the previous spicing function,
    starting with ``S2`` in the case of the first `spice`.

    To allow spicing, user of ``PatternMap`` class must provide `spice makers` - mapping between
    `spice` names and functions that generate spicing functions. E.g.:

    .. code-block:: python

       def create_spice_append_dot(previous_spice):
           def _spice(pattern, s):
               s = previous_spice(pattern, s)
               return s + '.'
           return _spice

    ``create_spice_append_dot`` is a `spice maker`, used during creation of a pattern map after
    its definition is read, ``_spice`` is the actual spicing function used during the transformation
    process.

    :param str filepath: Path to a YAML file with map definition.
    :param dict spices: apping between `spices` and their `makers`.
    :param libci.log.ContextLogger logger: Logger used for logging.
    """

    def __init__(self, filepath, spices=None, logger=None):
        self.logger = logger or Logging.get_logger()
        logger.connect(self)

        spices = spices or {}

        pattern_map = load_yaml(filepath, logger=self.logger)

        if pattern_map is None:
            raise CIError("pattern map '{}' does not contain any patterns".format(filepath))

        def _create_simple_repl(repl):
            def _replace(pattern, target):
                """
                Use `repl` to construct image from `target`, honoring all backreferences made by `pattern`.
                """

                self.debug("pattern '{}', repl '{}', target '{}'".format(pattern.pattern, repl, target))

                try:
                    return pattern.sub(repl, target)

                except re.error as e:
                    raise CIError("Cannot transform pattern '{}' with target '{}', repl '{}': {}".format(
                        pattern.pattern, target, repl, str(e)))

            return _replace

        self._compiled_map = []

        for pattern_dict in pattern_map:
            if not isinstance(pattern_dict, dict):
                raise CIError("Invalid format: '- <pattern>: <transform>' expected, '{}' found".format(pattern_dict))

            pattern = pattern_dict.keys()[0]
            converters = [s.strip() for s in pattern_dict[pattern].split(',')]

            # first item in `converters` is always a simple string used by `pattern.sub()` call
            converter = _create_simple_repl(converters.pop(0))

            # if there any any items left, they name "spices" to apply, one by one,
            # on the result of the first operation
            for spice in converters:
                if spice not in spices:
                    raise CIError("Unknown 'spice' function '{}'".format(spice))

                converter = spices[spice](converter)

            try:
                pattern = re.compile(pattern)

            except re.error as e:
                raise CIError("Pattern '{}' is not valid: {}".format(pattern, str(e)))

            self._compiled_map.append((pattern, converter))

    def match(self, s):
        """
        Try to match ``s`` by the map. If the match is found - the first one wins - then its
        transformation is applied to the ``s``.

        :rtype: str
        :returns: if matched, output of the corresponding transformation.
        """

        self.debug("trying to match string '{}' with patterns in the map".format(s))

        for pattern, converter in self._compiled_map:
            self.debug("testing pattern '{}'".format(pattern.pattern))

            match = pattern.match(s)
            if match is None:
                continue

            self.debug('  matched!')
            return converter(pattern, s)

        raise CIError("Could not match string '{}' with any pattern".format(s))


def wait(label, check, timeout=None, tick=30, logger=None):
    """
    Wait for a condition to be true.

    :param str label: printable label used for logging.
    :param callable check: called to test the condition. If its return value evaluates as ``True``,
        the condition is assumed to pass the test and waiting ends.
    :param int timeout: fail after this many seconds. ``None`` means test forever.
    :param int tick: test condition every ``tick`` seconds.
    :param libci.log.ContextAdapter logger: parent logger whose methods will be used for logging.
    :raises CIError: when ``timeout`` elapses while condition did not pass the check.
    """

    assert isinstance(tick, int) and tick > 0

    logger = logger or Logging.get_logger()

    if timeout is not None:
        end_time = time.time() + timeout

    def _timeout():
        return '{} seconds'.format(int(end_time - time.time())) if timeout is not None else 'infinite'

    logger.debug("waiting for condition '{}', timeout {} seconds, check every {} seconds".format(label, _timeout(),
                                                                                                 tick))

    while timeout is None or time.time() < end_time:
        logger.debug('{} left, sleeping for {} seconds'.format(_timeout(), tick))
        time.sleep(tick)

        ret = check()
        if ret:
            logger.debug('check passed, assuming success')
            return ret

        logger.debug('check failed, assuming failure')

    raise CIError("Condition '{}' failed to pass within given time".format(label))
