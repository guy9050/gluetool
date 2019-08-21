import os.path

import bs4

from libci.tests import *  # noqa


def check_loadable(glue, path, klass):
    python_mod = glue._import_pm(path, 'pytest_foo')

    assert hasattr(python_mod, klass)


def xml(text):
    return bs4.BeautifulSoup(text, 'xml').contents[0]


def testing_asset(*pieces):
    return os.path.join('gluetool_modules', 'tests', 'assets', *pieces)
