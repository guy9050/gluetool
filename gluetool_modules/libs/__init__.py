import argparse
import datetime
import os.path
import threading
import traceback
import enum

import gluetool.log
import gluetool.utils

from typing import Any, Callable, List, Optional  # noqa


class _UniqObject(object):
    """
    Simple class for unique, singleton objects. ``object()`` would fill the role as well,
    but it lacks the nice description when logged, hence adding ``__repr__`` to get fine
    logs.
    """

    def __init__(self, name):
        # type: (str) -> None

        self.name = name

    def __repr__(self):
        # type: () -> str

        return self.name


#: Represents a set of objects which "contains" any item it can possibly contain.
ANY = _UniqObject('<ANY>')


class StreamAdapter(gluetool.log.ContextAdapter):
    """
    Generic adapter used for logging stdout/stderr streams of commands by inspect callbacks.
    """

    def __init__(self, logger, name):
        # type: (gluetool.log.ContextAdapter, str) -> None

        super(StreamAdapter, self).__init__(logger, {'ctx_stream': (100, name)})


class GlueEnum(enum.Enum):

    def __str__(self):
        # type: () -> str

        return self.name

    def __repr__(self):
        # type: () -> str

        return self.name


class StreamHandler(argparse.Namespace):
    """
    Dummy class bundling together a logging function and a buffer of characters. The `write` method
    dumps the content of buffer.

    Used by inspect callbacks when logging real-time output of commands - inspect callbacks updates
    buffer by incoming data, once it spots a new-line, flushes the buffer to the output.
    """

    def write(self):
        # type: () -> None

        self.log_fn(''.join(self.buff))
        self.buff = []  # type: List[str]


def is_recursion(filepath, function_name):
    # type: (str, str) -> bool
    """
    Check whether the caller haven't been called already before, detecting the possible recursion.

    When function's interested whether it's being called recursively, it simply calls this helper
    with its ``__file__`` and the current function name.

    :param str filepath: filepath of the caller site.
    :param str function_name: name of the caller.
    :returns: ``True`` if the recursion was detected.
    """

    # For frames of the stack, we check whether a frame lies in the calling functon. If that's true
    # for any frame except the previous one (the last one is this function, the previous one is the
    # caller which is interested in the check), it means the previous frame is not the first frame
    # running the caller function.

    file_split = os.path.splitext(filepath)[0]

    return any([
        (f[2] == function_name and os.path.splitext(f[0])[0] == file_split)
        for f in traceback.extract_stack()[0:-2]
    ])


_strptime_lock = threading.Lock()


def strptime(*args, **kwargs):
    # type: (*Any, **Any) -> Any
    """
    ``datetime.datetime.strptime`` in Python 2.7 is slightly broken when it comes to multithreading.
    See [1] and [2] for details. It will not be fixed, since it's limited to Python 2 only, and it appears
    only when multiple threads call this library function, so, this is an attempt of a thread-safe wrapper
    that would serialize access to this library function.

    It's really crude: there's a single lock guarding the access to the library function, all calls are serialized.
    Given that ``strptime`` isn't really critical, we should be able to get away with this single contestion point.

    [1] 'module' object has no attribute '_strptime'
    [2] https://bugs.python.org/issue7980
    """

    with _strptime_lock:
        return datetime.datetime.strptime(*args, **kwargs)


def create_inspect_callback(logger):
    # type: (gluetool.log.ContextAdapter) -> Callable[[gluetool.utils.StreamReader, Optional[str], bool], None]

    # Note that we're using `warn` for stderr, to make it pop up in the output.
    streams = {
        '<stdout>': StreamHandler(buff=[], log_fn=StreamAdapter(logger, 'stdout').info),
        '<stderr>': StreamHandler(buff=[], log_fn=StreamAdapter(logger, 'stderr').warn)
    }

    def _callback(stream, data, flush=False):
        # type: (gluetool.utils.StreamReader, Optional[str], bool) -> None

        stream_handler = streams[stream.name]

        if flush and stream_handler.buff:
            stream_handler.write()
            return

        if data is None:
            return

        for c in data:
            if c == '\n':
                stream_handler.write()

            elif c == '\r':
                continue

            else:
                stream_handler.buff.append(c)

    return _callback
