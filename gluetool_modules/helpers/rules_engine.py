import re
import ast

import gluetool
from gluetool import GlueError, SoftGlueError
from gluetool.log import log_dict
from gluetool.utils import cached_property, load_yaml, normalize_multistring_option, dict_update
import _ast

# Type annotations
# pylint: disable=unused-import,wrong-import-order,invalid-name
from typing import cast, Any, Callable, Dict, Iterator, List, Match, Optional, Tuple, Union  # noqa

EntryType = Dict[str, Any]  # noqa
ContextType = Dict[str, Any]  # noqa
ContextGetterType = Callable[[], ContextType]  # noqa
CommandCallbackType = Callable[[EntryType, str, Any, ContextType], bool]  # noqa


class RulesError(SoftGlueError):
    """
    Base class of rules-related soft exceptions.

    :param str message: descriptive message, passed to parent Exception classes.
    :param Rules rules: rules in question.
    :param str intro: introductory text, pasted at the beginning of template.
    :param str error: specific error message.
    """

    def __init__(self, message, rules, intro, error):
        # type: (str, str, str, str) -> None

        super(RulesError, self).__init__(message)

        self.rules = rules
        self.intro = intro
        self.error = error


class InvalidASTNodeError(RulesError):
    def __init__(self, rules, node):
        # type: (str, ast.AST) -> None

        super(InvalidASTNodeError, self).__init__(
            "It is not allowed to use '{}' in rules".format(node.__class__.__name__),
            rules,
            'Dangerous and disallowed node used in rules',
            "It is not allowed to use '{}' in rules.".format(node.__class__.__name__))


class RulesSyntaxError(RulesError):
    def __init__(self, rules, exc):
        # type: (str, SyntaxError) -> None

        super(RulesSyntaxError, self).__init__(
            'Cannot parse rules',
            rules,
            "Cannot parse rules '{}'".format(rules),
            'Position {}:{}: {}'.format(exc.lineno, exc.offset, exc))


class RulesTypeError(RulesError):
    def __init__(self, rules, exc):
        # type: (str, Exception) -> None

        super(RulesTypeError, self).__init__(
            'Cannot parse rules',
            rules,
            "Cannot parse rules '{}'".format(rules),
            str(exc))


class RulesASTVisitor(ast.NodeTransformer):
    """
    Custom AST visitor, making sure no disallowed nodes are present in the rules' AST.
    """

    _valid_classes = tuple([
        getattr(_ast, node_class) for node_class in (
            'Expression', 'Expr', 'Compare', 'Name', 'Load', 'BoolOp', 'UnaryOp',
            'Str', 'Num', 'List', 'Tuple', 'Dict',
            'Subscript', 'Index', 'ListComp', 'comprehension',
            'Store',
            'Eq', 'NotEq', 'Lt', 'LtE', 'Gt', 'GtE', 'Is', 'IsNot', 'In', 'NotIn',
            'And', 'Or', 'Not',
            'IfExp',
            'Attribute', 'Call'
        )
    ])

    def __init__(self, rules):
        # type: (Rules) -> None

        super(RulesASTVisitor, self).__init__()

        self._rules = rules

    def generic_visit(self, node):
        # type: (ast.AST) -> Any

        if not isinstance(node, RulesASTVisitor._valid_classes):
            # pylint: disable=protected-access
            raise InvalidASTNodeError(self._rules._rules, node)

        return super(RulesASTVisitor, self).generic_visit(node)


class MatchableString(str):
    """
    Enhanced string - it has all methods and properties of a string, provides
    :py:ref:`re.match` and :py:ref:`re.search` as instance methods.
    """

    # pylint: disable=invalid-name
    def match(self, pattern, I=True):
        # type: (str, bool) -> Optional[Match[Any]]

        return re.match(pattern, str(self), re.I if I is True else 0)

    def search(self, pattern, I=True):
        # type: (str, bool) -> Optional[Match[Any]]

        return re.search(pattern, str(self), re.I if I is True else 0)


class Rules(object):
    # pylint: disable=too-few-public-methods

    """
    Wrap compilation and evaluation of filtering rules.

    :param str rules: Rule is a Python expression that could be evaluated.
    """

    def __init__(self, rules):
        # type: (str) -> None

        self._rules = rules
        self._code = None  # type: Any

    def __repr__(self):
        # type: () -> str

        return '<Rules: {}>'.format(self._rules)

    def _compile(self):
        # type: () -> Any
        """
        Compile rule. Parse rule into an AST, perform its sanity checks,
        and then compile it into executable.
        """

        try:
            tree = ast.parse(self._rules, mode='eval')

        except SyntaxError as exc:
            raise RulesSyntaxError(self._rules, exc)

        except TypeError as e:
            raise RulesTypeError(self._rules, e)

        RulesASTVisitor(self).visit(tree)

        try:
            return compile(tree, '<static-config-file>', 'eval')

        # This bit will probably be left uncovered by unit tests - the best way forward seems to be patching
        # `compile` and injecting an error, but `compile` is an builtin function and pytest might be using
        # it internaly (or may start in the future...). Not a good idea to poke into that.
        except Exception as e:
            raise RulesTypeError(self._rules, e)

    def eval(self, our_globals, our_locals):
        # type: (ContextType, ContextType) -> Any
        """
        Evaluate rule. User must provide both `locals` and `globals` dictionaries
        we use as a context for the rule.
        """

        if self._code is None:
            self._code = self._compile()

        # eval is dangerous. This time I hope it's safe-guarded by AST filtering...
        try:
            # pylint: disable=eval-used
            return eval(self._code, our_globals, our_locals)

        except NameError as exc:
            raise gluetool.GlueError('Unknown variable used in rule: {}'.format(exc.message))


class RulesEngine(gluetool.Module):
    """
    Simple "rule" evaluation engine. Allows users to use subset of Python language
    in their configuration, for example to decide which section of a config file to
    use. Module using such configuration just need to provide necessary context, e.g.
    objects that are available to the rules the module supports.

    To write rules, a restricted set of Python expressions is provided. Following
    Python constructs are allowed:

        * comparisons: ``==``, ``<=``, ``not in``, etc.
        * strings, numbers, lists, tuples
        * logic operators: ``and``, ``or``, ``not``
        * ``... if ... else ...`` expressions
        * calling a function or method

    Strings have two extra methods, providing access to regular expression functionality:

        * ``match(pattern, I=True)``
        * ``search(pattern, I=True)``

    Helper function ``EXISTS`` is provided as well, returning ``True`` when the given
    variable name exists in the context.

    Users of this module would simply specify what objects are available to rules in their
    domain, and then provides these objects when asking ``rules-engine`` (via the shared
    function) to evaluate the rules.

    For example, a module M promises its users that current user's username would be
    available to rules M is using for its functionality, as a variable ``USERNAME``.
    Such rules can then look like ``USERNAME.match('f.*')``, or ``USERNAME == 'foo'``.
    If M is used by user named ``foobar``, the first rule would evaluate to ``True``,
    while the second would be false-ish.
    """

    name = 'rules-engine'
    description = 'Evaluate simple Python-like rules.'

    options = {
        'rules': {
            'help': 'Rules to evaluate when module is executed. Used for testing (default: %(default)s).',
            'default': None
        },
        'variables': {
            'help': 'File(s) with additional context objects (default: none).',
            'action': 'append',
            'default': []
        }
    }

    shared_functions = ['evaluate_rules', 'evaluate_filter', 'evaluate_instructions']

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    def _filter(self,
                entries,  # type: List[EntryType]
                context=None,  # type: Optional[Union[ContextType, ContextGetterType]]
                default_rule='True',  # type: str
                stop_at_first_hit=False  # type: bool
               ):  # noqa
        # type: (...) -> Iterator[Tuple[EntryType, ContextType]]
        """
        Yields entries that are allowed by their rules.

        This is an internal implementation of a common functionality: find out what entries are valid
        with respect to their rules. The method is used to simplify other - public - methods.

        :param list(dict) entries: List of entries to filter.
        :param context: Provider of context for rules and templating services. Either a dictionary or a callable
            returning a dictionary. If callable is provided, it will be called before each entry to
            refresh the context.
        :param str default_rule: If there's no rule in the instruction, this will be used. For example, use ``"False"``
            to skip instructions without rules.
        :param bool stop_at_first_hit: If set, first entry whose rule evaluated true-ishly is returned immediately.
        :rtype: Iterator[tuple(dict, dict)]
        :returns: yields tuples of two items: the entry and the context used in its evaluation.
        """

        # If we don't have a context, get one from the core.
        if context is None:
            context = self.shared('eval_context')

        # For the sake of simplicity, the loop over instructions will always call context_getter. It's either
        # callable given by caller, or a simple anonymous function returning a dictionary - either the one
        # given by caller or the default from above.
        if callable(context):
            context_getter = context

        else:
            context_getter = cast(ContextGetterType, lambda: context)

        for entry in entries:
            loop_context = context_getter()

            log_dict(self.debug, 'entry', entry)

            # Not calling `self.evaluate_rules` directly - other modules may have overload this shared function,
            # let's use the correct implementation.
            if not self.shared('evaluate_rules', entry.get('rule', default_rule), context=loop_context):
                self.debug('denied by rules')
                continue

            yield entry, loop_context

            if stop_at_first_hit:
                break

    @cached_property
    def variables(self):
        # type: () -> Any

        configs = normalize_multistring_option(self.option('variables'))

        variables = {}  # type: Any

        for config in configs:
            variables.update(load_yaml(config, logger=self.logger))

        return variables

    def evaluate_rules(self, rules, context=None):
        # type: (str, Optional[ContextType]) -> Any
        """
        Evaluate rules to a single value (usualy bool-ish - ``True``/``False``, (non-)empty string, etc.),
        within a context provided by the caller via ``context`` mapping.
        Keys and values in the mapping are passed to internal ``eval`` implementation, making them
        available to the rules.

        :param str rules: rules to evaluate.
        :param dict context: mapping of names and object caller wants to be available to rules.
        :returns: whatever comes out from rules evaluation.
        """

        # pylint: disable=no-self-use

        def _enhance_strings(variables):
            # type: (ContextType) -> ContextType

            return {
                key: MatchableString(value) if isinstance(value, str) else value for key, value in variables.iteritems()
            }

        if context is None:
            context = dict_update({}, self.variables)
        else:
            context = dict_update({}, self.variables, context)

        custom_locals = _enhance_strings(context)

        custom_locals['EXISTS'] = lambda name: name in custom_locals

        self.debug('rules: {}'.format(rules))
        log_dict(self.verbose, 'locals', custom_locals)

        result = Rules(rules).eval({}, custom_locals)

        log_dict(self.debug, 'eval result', result)

        return result

    # pylint: disable=too-many-arguments
    def evaluate_filter(self, entries, context=None, default_rule='True',
                        stop_at_first_hit=False):
        # type: (List[EntryType], Optional[Union[ContextType, ContextGetterType]], str, bool) -> List[EntryType]
        """
        Find out what entries of the list are allowed by their rules, and return them.

        An entry is a simple dictionary with arbitrary keys. If there is a key named ``rule``, it
        is evaluated and when the result is false-ish, the entry does not make the cut. The list of
        entries that are allowed by their rules is returned.

        .. code-block:: yaml

           - rule: ...
             <key #1>: ...
             <key #2>: ...

        :param list(dict) entries: List of entries to filter.
        :param context: Provider of context for rules and templating services. Either a dictionary or a callable
            returning a dictionary. If callable is provided, it will be called before each entry to
            refresh the context.
        :param str default_rule: If there's no rule in the instruction, this will be used. For example, use ``False``
            to skip instructions without rules.
        :param bool stop_at_first_hit: If set, first entry whose rule evaluated true-ishly is returned immediately.
        :rtype: list(dict)
        :returns: List of entries that passed through the filter.
        """

        instruction_iterator = self._filter(
            entries, context=context, default_rule=default_rule, stop_at_first_hit=stop_at_first_hit
        )

        return [
            entry for entry, _ in instruction_iterator
        ]

    # pylint: disable=too-many-arguments
    def evaluate_instructions(self,
                              instructions,  # type: List[EntryType]
                              commands,  # type: Dict[str, CommandCallbackType]
                              context=None,  # type: Optional[Union[ContextType, ContextGetterType]]
                              default_rule='True',  # type: str
                              stop_at_first_hit=False,  # type: bool
                              ignore_unhandled_commands=False  # type: bool
                             ):  # noqa
        # type: (...) -> None
        """
        Evaluate "instructions", using given callbacks to perform commands ordered by instructions.

        An instruction is a simple dictionary with arbitrary keys, "commands". If there is a key named ``rule``, it
        is evaluated and when the result is false-ish, the instruction is skipped.

        .. code-block:: yaml

           - rule:
             <command #1>: ...
             <command #2>: ...

        Instructions are inspected in order they are given by the caller, and unless denied by the optional rule,
        the instruction commands are looked up in the ``commands`` mapping, and found callbacks are called,
        with the current instruction, command, its value and a context rules-engine used to evaluate instruction
        rule as arguments.

        .. code-block:: yaml

           - rule: True
             log: some dummy message

        .. code-block:: python

           def foo(self, instruction, command, argument, context):
               self.info(argument)

           self.shared('evaluate_instructions', <instructions loaded from a file>, {'log': foo})

        ``foo`` callback will be called like this:

        .. code-block:: python

           foo(instruction, 'log', 'some dummy message', context_used_by_rules_engine)

        :param list(dict) instructions: List of instructions to follow.
        :param dict(str, callable(dict, str, object, dict)) commands: Mapping between command names and their
            callbacks.
        :param context: Provider of context for rules and templating services. Either a dictionary or a callable
            returning a dictionary. If callable is provided, it will be called before each instruction to
            refresh the context.
        :param str default_rule: If there's no rule in the instruction, this will be used. For example, use ``False``
            to skip instructions without rules.
        :param bool stop_at_first_hit: If set, first command callback returning ``True`` will cause the function
            to skip remaining commands and start with the next instruction.
        :param bool ignore_unhandled_commands: If set, commands without any callbacks will be ignored. otherwise,
            an exception will be raised.
        """

        # Oops, `stop_at_first_hit` means something different to this method than to `_filter` :/
        # `_filter`'s `stop_at_first_hit` cannot be expressed by parameters of this method,
        # therefore defaulting to `False`, letting `_filter` process all instructions.
        instruction_iterator = self._filter(
            instructions, context=context, default_rule=default_rule, stop_at_first_hit=False
        )

        for instruction, instruction_context in instruction_iterator:
            for command, argument in instruction.iteritems():
                if command == 'rule':
                    continue

                callback = commands.get(command, None)

                if not callback:
                    msg = "No callback for command '{}'".format(command)

                    if ignore_unhandled_commands:
                        self.warn(msg)
                        continue

                    raise GlueError(msg)

                result = callback(instruction, command, argument, instruction_context)

                if result is True and stop_at_first_hit:
                    self.debug('command handled and we should stop at first hit')
                    break

    def execute(self):
        # type: () -> None

        if not self.option('rules'):
            return

        self.info('rules evaluate to: {}'.format(self.evaluate_rules(self.option('rules'))))
