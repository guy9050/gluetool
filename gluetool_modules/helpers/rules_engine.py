import re
import ast

import gluetool
from gluetool import GlueError, SoftGlueError
from gluetool.log import log_dict

import _ast


class RulesError(SoftGlueError):
    """
    Base class of rules-related soft exceptions.

    :param str message: descriptive message, passed to parent Exception classes.
    :param Rules rules: rules in question.
    :param str intro: introductory text, pasted at the beginning of template.
    :param str error: specific error message.
    """

    def __init__(self, message, rules, intro, error):
        super(RulesError, self).__init__(message)

        self.rules = rules
        self.intro = intro
        self.error = error


class InvalidASTNodeError(RulesError):
    def __init__(self, rules, node):
        super(InvalidASTNodeError, self).__init__(
            "It is not allowed to use '{}' in rules".format(node.__class__.__name__),
            rules,
            'Dangerous and disallowed node used in rules',
            "It is not allowed to use '{}' in rules.".format(node.__class__.__name__))


class RulesSyntaxError(RulesError):
    def __init__(self, rules, exc):
        super(RulesSyntaxError, self).__init__(
            'Cannot parse rules',
            rules,
            "Cannot parse rules '{}'".format(rules),
            'Position {}:{}: {}'.format(exc.lineno, exc.offset, exc))


class RulesTypeError(RulesError):
    def __init__(self, rules, exc):
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
            'Attribute', 'Call'
        )
    ])

    def __init__(self, rules, *args, **kwargs):
        super(RulesASTVisitor, self).__init__(*args, **kwargs)

        self._rules = rules

    def generic_visit(self, node):
        if not isinstance(node, RulesASTVisitor._valid_classes):
            raise InvalidASTNodeError(self._rules, node)

        return super(RulesASTVisitor, self).generic_visit(node)


class MatchableString(str):
    """
    Enhanced string - it has all methods and properties of a string, provides
    :py:ref:`re.match` and :py:ref:`re.search` as instance methods.
    """

    # pylint: disable=invalid-name
    def match(self, pattern, I=True):
        return re.match(pattern, str(self), re.I if I is True else 0)

    def search(self, pattern, I=True):
        return re.search(pattern, str(self), re.I if I is True else 0)


class Rules(object):
    # pylint: disable=too-few-public-methods

    """
    Wrap compilation and evaluation of filtering rules.

    :param str rules: Rule is a Python expression that could be evaluated.
    """

    def __init__(self, rules):
        self._rules = rules
        self._code = None

    def __repr__(self):
        return '<Rules: {}>'.format(self._rules)

    def _compile(self):
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

        except Exception as e:
            raise RulesTypeError(self._rules, e)

    def eval(self, our_globals, our_locals):
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
        }
    }

    shared_functions = ('evaluate_rules', 'evaluate_instructions')

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    def evaluate_rules(self, rules, context=None):
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
            return {
                key: MatchableString(value) if isinstance(value, str) else value for key, value in variables.iteritems()
            }

        custom_locals = _enhance_strings(context or {})

        custom_locals['EXISTS'] = lambda name: name in custom_locals

        self.debug('rules: {}'.format(rules))
        log_dict(self.debug, 'locals', custom_locals)

        result = Rules(rules).eval({}, custom_locals)

        log_dict(self.debug, 'eval result', result)

        return result

    # pylint: disable=too-many-arguments
    def evaluate_instructions(self, instructions, commands, context=None,
                              default_rule='True',
                              stop_at_first_hit=False,
                              ignore_unhandled_commands=False):
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

        # If we don't have a context, get one from the core.
        if context is None:
            context = self.shared('eval_context')

        # For the sake of simplicity, the loop over instructions will always call context_getter. It's either
        # callable given by caller, or a simple anonymous function returning a dictionary - either the one
        # given by caller or the default from above.
        context_getter = context if callable(context) else lambda: context

        for instruction in instructions:
            loop_context = context_getter()

            log_dict(self.debug, 'instruction', instruction)

            if not self.shared('evaluate_rules', instruction.get('rule', default_rule), context=loop_context):
                self.debug('denied by rules')
                continue

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

                result = callback(instruction, command, argument, loop_context)

                if result is True and stop_at_first_hit:
                    self.debug('command handled and we should stop at first hit')
                    break

    def execute(self):
        if not self.option('rules'):
            return

        self.info('rules evaluate to: {}'.format(self.evaluate_rules(self.option('rules'))))
