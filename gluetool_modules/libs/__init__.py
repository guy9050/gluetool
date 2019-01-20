import os.path
import traceback


class _UniqObject(object):
    """
    Simple class for unique, singleton objects. ``object()`` would fill the role as well,
    but it lacks the nice description when logged, hence adding ``__repr__`` to get fine
    logs.
    """

    # pylint: disable=too-few-public-methods
    def __init__(self, name):
        # type: (str) -> None

        self.name = name

    def __repr__(self):
        # type: () -> str

        return self.name


#: Represents a set of objects which "contains" any item it can possibly contain.
ANY = _UniqObject('<ANY>')


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
