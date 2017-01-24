import pytest

import libci


def test_import_sanity():
    libci.CI()


def test_check_for_commands():
    commands = ('ls', 'gzip')

    # these should exist...
    for cmd in commands:
        libci.utils.check_for_commands([cmd])

    # ... and these probably not.
    for cmd in commands:
        cmd = 'does-not-exists-' + cmd

        with pytest.raises(libci.CIError, message='\'{0}\' command not found on the system'.format(cmd)):
            libci.utils.check_for_commands([cmd])
