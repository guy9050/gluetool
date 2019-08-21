"""
Common API for modules providing ``setup_guest``  shared functions.

The ``setup_guest`` should perform actions to prepare the given guest for more work. The whole process is often
implemented by multiple modules in a chain. To ease cooperation, this module provides simple definition of
structure all ``setup_guest`` functions should return, and states few basic rules.

* every ``setup_guest`` function returns a list of :py:class:`GuestSetupOutput`.
* if ``setup_guest`` function called its predecessors, its output structures should be added to the list it obtained
  from the predecessors.
* every ``setup_guest`` function focuses on a single guest at a time. If user needs a parallel execution,
  we can provide :py:mod:`gluetool_modules.libs.jobs` for that purpose.
* every ``setup_guest`` should accept ``log_dirpath`` parameter - all files produced by the setup process should
  land in this directory. Module is free to create subdirectories for subtasks.
"""

import os

# Type annotations
from typing import TYPE_CHECKING, Any, NamedTuple, Optional  # noqa

if TYPE_CHECKING:
    import libci.guest  # noqa


#: Represents one action taken by "guest setup" module and pointer to its logs.
#:
#: :ivar str label: human-readable name for what this particular guest setup bit represents.
#: :ivar str log_path: local path to a directory or file where log lives.
#: :ivar additional_data: anything else module considers interesting for its users.
GuestSetupOutput = NamedTuple('GuestSetupOutput', (
    ('label', str),
    ('log_path', str),
    ('additional_data', Any)
))


def guest_setup_log_dirpath(guest, log_dirpath):
    # type: (libci.guest.NetworkedGuest, Optional[str]) -> str

    if not log_dirpath:
        log_dirpath = 'guest-setup-{}'.format(guest.name)

    if not os.path.exists(log_dirpath):
        os.mkdir(log_dirpath)

    return log_dirpath
