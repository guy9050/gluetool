import collections
import logging

import gluetool


#: A note.
#:
#: :param str text: text of the note.
#: :param int level: level of the note. Using well-known levels of ``logging``.
Note = collections.namedtuple('Note', ['text', 'level'])


class Notes(gluetool.Module):
    """
    Store various notes and warnings, gathered by other modules. The notes are than available
    in the evaluation context under ``NOTES`` key.

    Each note has a string text and a integer representing its `level`. Any integer can be used,
    using levels defined by :py:mod:`logging` module, e.g. ``logging.INFO`` or ``logging.WARN``,
    is recommended.
    """

    name = 'notes'
    description = 'Store various notes and warnings, gahthered by other modules.'

    shared_functions = ('add_note',)

    def __init__(self, *args, **kwargs):
        # type: (*Any, **Any) -> None

        super(Notes, self).__init__(*args, **kwargs)

        self._notes = []

    def add_note(self, text, level=logging.INFO):
        # type: (str, int) -> None
        """
        Add new note.

        :param str text: Text of the note.
        :param int level: Level of the note. Any integer is acceptable, using levels defined by :py:mod:`logging`
            module, e.g. ``logging.DEBUG`` or ``logging.INFO``, is recommended.
        """

        self._notes.append(Note(text=text, level=level))

    @property
    def eval_context(self):
        # pylint: disable=unused-variable
        __content__ = {  # noqa
            'NOTES': """
                     List of all gathered notes, sorted by their levels from the more important levels
                     (higher values, e.g. ``logging.ERROR``) down to the lesser important ones (lower values,
                     e.g. ``logging.DEBUG``). Each note has ``text`` and ``level`` properties.
                     """
        }

        return {
            'NOTES': sorted(self._notes, key=lambda x: x.level, reversed=True)
        }
