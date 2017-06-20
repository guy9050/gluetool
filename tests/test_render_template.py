# pylint: disable=blacklisted-name

import pytest

import libci
from libci.utils import render_template

from mako.template import Template


TMPL_STR = """
This is a dummy string template: {foo}
"""


TMPL_MAKO = Template("""
This is a dummy Mako template: ${bar}
""")


def test_render_string():
    assert render_template(TMPL_STR, foo='baz') == 'This is a dummy string template: baz'


def test_render_mako():
    assert render_template(TMPL_MAKO, bar='baz') == 'This is a dummy Mako template: baz'


def test_unexpected_template_type():
    with pytest.raises(libci.CIError, message="Unhandled template type <type 'unicode'>"):
        render_template(u'fake template')


def test_missing_variable_string():
    with pytest.raises(libci.CIError):
        render_template(TMPL_STR)


def test_missing_variable_mako():
    with pytest.raises(libci.CIError):
        render_template(TMPL_MAKO)
