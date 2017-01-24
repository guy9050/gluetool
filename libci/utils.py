"""
Various helpers.
"""

import subprocess
from libci import CIError

try:
    from subprocess import DEVNULL
except ImportError:
    import os
    DEVNULL = open(os.devnull, 'wb')


def check_for_commands(cmds):
    """ Checks if all commands in list cmds are valid """
    for cmd in cmds:
        p = subprocess.Popen(['command', '-v', cmd], stdout=DEVNULL, shell=True)
        retcode = p.wait()
        p.communicate()
        if retcode != 0:
            msg = '\'{}\' command not found on the system'.format(cmd)
            raise CIError(msg)
