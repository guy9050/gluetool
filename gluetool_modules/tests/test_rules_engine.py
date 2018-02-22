import re
import types

import pytest

import gluetool_modules.helpers.rules_engine
from gluetool_modules.helpers.rules_engine import RulesEngine, Rules, MatchableString, RulesSyntaxError, InvalidASTNodeError

from mock import MagicMock
from . import create_module, check_loadable


@pytest.fixture(name='module')
def fixture_module():
    _, module = create_module(RulesEngine)

    return module


def test_loadable(module):
    check_loadable(module.glue, 'helpers/rules_engine', 'gluetool_modules/helpers/rules_engine.py', 'RulesEngine')


def test_shared(module):
    assert module.glue.has_shared('evaluate_rules') is True


def test_matchable_string_inheritance():
    s = MatchableString('foo')

    assert isinstance(s, str)


@pytest.mark.parametrize('regex_method, kwargs, expected_args', [
    ('match',  {},           (re.I,)),
    ('match',  {'I': True},  (re.I,)),
    ('match',  {'I': False}, (0,)),
    ('search', {},           (re.I,)),
    ('search', {'I': True},  (re.I,)),
    ('search', {'I': False}, (0,))
])
def test_matchable_string_regex(monkeypatch, regex_method, kwargs, expected_args):
    s = MatchableString('foo')

    mock_method = MagicMock()
    monkeypatch.setattr('gluetool_modules.helpers.rules_engine.re.{}'.format(regex_method), mock_method)

    getattr(s, regex_method)('bar', **kwargs)

    mock_method.assert_called_once_with('bar', 'foo', *expected_args)


@pytest.mark.parametrize('rule', [
    '1 == 1'
])
def test_compile_sanity(rule):
    code = Rules(rule)._compile()

    assert isinstance(code, types.CodeType)


@pytest.mark.parametrize('rule, error_klass, error_message, error_detail', [
    (
        '1 == ',
        RulesSyntaxError,
        r'Cannot parse rules',
        'Position 1:5: unexpected EOF while parsing (<unknown>, line 1)'
    ),
    (
        '1 * 1',
        InvalidASTNodeError,
        r"It is not allowed to use 'BinOp' in rules",
        "It is not allowed to use 'BinOp' in rules."
    )
])
def test_compile_error(rule, error_klass, error_message, error_detail):
    with pytest.raises(error_klass, match=error_message) as excinfo:
        Rules(rule)._compile()

    assert excinfo.value.error == error_detail


@pytest.mark.parametrize('rule, context, outcome', [
    (
        '1 == 1', {}, True
    ),
    (
        '1 == 2', {}, False
    ),
    (
        "FOO.match('bar')", {'FOO': MatchableString('foo')}, None
    ),
    (
        "FOO.match('bar') is None", {'FOO': MatchableString('bar')}, False
    ),
    (
        "FOO.match('bar') is None", {'FOO': MatchableString('foo')}, True
    ),
    (
        "not FOO.match('bar')", {'FOO': MatchableString('bar')}, False
    )
])
def test_eval(rule, context, outcome):
    assert Rules(rule).eval({}, context) == outcome
