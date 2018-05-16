import os.path

import bs4

# pylint: disable=wildcard-import
from libci.tests import *  # noqa


def check_loadable(glue, group, path, klass):
    # pylint: disable=protected-access
    python_mod = glue._load_python_module(group, 'pytest_foo', path)

    assert hasattr(python_mod, klass)


def xml(text):
    return bs4.BeautifulSoup(text, 'xml').contents[0]


def testing_asset(*pieces):
    return os.path.join('gluetool_modules', 'tests', 'assets', *pieces)
