class _UniqObject(object):
    """
    Simple class for unique, singleton objects. ``object()`` would fill the role as well,
    but it lacks the nice description when logged, hence adding ``__repr__`` to get fine
    logs.
    """

    # pylint: disable=too-few-public-methods
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


#: Represents a set of objects which "contains" any item it can possibly contain.
ANY = _UniqObject('<ANY>')
