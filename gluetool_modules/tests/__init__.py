import bs4

# pylint: disable=wildcard-import
from libci.tests import *  # noqa


def xml(text):
    return bs4.BeautifulSoup(text, 'xml').contents[0]
