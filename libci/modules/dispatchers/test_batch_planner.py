import re
import shlex
import ast
import _ast

import libci
from libci import Module, CIError, SoftCIError
from libci.log import format_dict, log_dict
from libci.utils import cached_property, load_yaml


class RulesError(SoftCIError):
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


class CommandsError(SoftCIError):
    """
    Base class of commands-related soft exceptions.

    :param str message: descriptive message, passed to parent Exception classes.
    :param obj commands: commands in question. Will be formatted and pasted into
      the template.
    """

    def __init__(self, message, commands):
        super(CommandsError, self).__init__(message)

        self.commands = commands


class InvalidASTNodeError(RulesError):
    def __init__(self, rules, node):
        super(InvalidASTNodeError, self).__init__(
            'Node of type {} not allowed in rules'.format(node.__class__.__name__),
            rules,
            'Filtering rules employed for component configuration are using disallowed node',
            'Node of class {} is not allowed or supported.'.format(node.__class__.__name__))


class UnsupportedASTCallError(RulesError):
    def __init__(self, rules, node):
        super(UnsupportedASTCallError, self).__init__(
            'Calling function {} not allowed in rules'.format(node.func.id),
            rules,
            'Filtering rules employed for component configuration are calling unsupported function'
            "Function '{}' is not supported.".format(node.func.id))


class RulesSyntaxError(RulesError):
    def __init__(self, rules, exc):
        super(RulesSyntaxError, self).__init__(
            "Cannot parse rules '{}': line {}, offset {}".format(rules, exc.lineno, exc.offset),
            rules,
            'Syntax error raised while dealing with filtering rules',
            'line {}, offset {}'.format(exc.lineno, exc.offset))


class RulesTypeError(RulesError):
    def __init__(self, rules, exc):
        super(RulesTypeError, self).__init__(
            "Cannot parse rules '{}': {}".format(rules, str(exc)),
            rules,
            'Cannot parse filtering rules',
            str(exc))


class NoFilteringRulesError(CommandsError):
    def __init__(self, name, commands):
        super(NoFilteringRulesError, self).__init__(
            "Command set '{}' does not contain any filtering rules".format(name),
            commands)


class UnexpectedConfigDataError(CommandsError):
    def __init__(self, commands):
        super(UnexpectedConfigDataError, self).__init__(
            'Unexpected command or structures found in config file',
            commands)


class SanityASTVisitor(ast.NodeVisitor):
    """
    Custom AST visitor. It's only purpose is to visit every node in the tree,
    and verify that there's no disallowed node. We don't want to allow stuff
    like calling functions, and limit rules to basic expressions.
    """

    _valid_classes = tuple([
        getattr(_ast, node_class) for node_class in (
            'Expression', 'Expr', 'Compare', 'Name', 'Load', 'BoolOp',
            'Str', 'Num', 'List', 'Tuple',
            'Eq', 'NotEq', 'Lt', 'LtE', 'Gt', 'GtE', 'Is', 'IsNot', 'In', 'NotIn',
            'And', 'Or', 'Not',
            'Call'
        )
    ])

    _valid_functions = ('MATCH',)

    def __init__(self, rules, *args, **kwargs):
        super(SanityASTVisitor, self).__init__(*args, **kwargs)

        self._rules = rules

    def generic_visit(self, node):
        if not isinstance(node, SanityASTVisitor._valid_classes):
            raise InvalidASTNodeError(self._rules, node)

        if isinstance(node, _ast.Call) and node.func.id not in SanityASTVisitor._valid_functions:
            raise UnsupportedASTCallError(self._rules, node)

        super(SanityASTVisitor, self).generic_visit(node)


class Rules(object):
    # pylint: disable=too-few-public-methods

    """
    Wrap compilation and evaluation of filtering rules.

    :param str rules: Rule is a Python expression that could be evaluated. If rule
      evaluates into anything but `True`, we consider it as denial.
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

        except SyntaxError as e:
            raise RulesSyntaxError(self._rules, e)

        except TypeError as e:
            raise RulesTypeError(self._rules, e)

        SanityASTVisitor(self).visit(tree)

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
        # pylint: disable=eval-used
        return eval(self._code, our_globals, our_locals)


class TestBatchPlanner(Module):
    """
    Provides different methods of finding out *what* jobs (and tests) should be started
    for a artifact. Allows use of multiple methods in sequence - when the first one is
    unable to find the answer, the next method in the list is used, and so on.

    Currently, these methods are provided:

    * ``static-config``: use a YAML file (set by ``--config`` option) to specify what jobs
      are supposed to be run for artifacts.
    """

    # Supported flags - keep them alphabetically sorted
    KNOWN_FLAGS = ('apply-all', 'options', 'recipients')

    name = 'test-batch-planner'
    description = 'Configurable test batch planner.'

    options = {
        'methods': {
            'help': 'Comma-separated list of methods.',
            'metavar': 'METHOD',
            'action': 'append',
            'choices': ['static-config'],
            'default': []
        },
        'config': {
            'help': 'Static configuration for components.'
        },
        'job-result-type': {
            'help': 'List of comma-separated pairs <job>:<result type>',
            'action': 'append',
            'default': []
        }
    }

    required_options = ('methods',)

    shared_functions = ('plan_test_batch',)

    @cached_property
    def job_result_types(self):
        # we accept multiple --job-result-type options, and when set in config
        # file, one can have multiple pairs...

        mapping = {}

        value = self.option('job-result-type')

        if isinstance(value, str):
            value = [value]

        for entry in value:
            for pair in entry.strip().split(','):
                job, result_type = pair.strip().split(':')

                job = job.strip()
                result_type = result_type.strip()

                mapping[job] = result_type
                self.debug("job '{}' provides results of type '{}'".format(job, result_type))

        return mapping

    @cached_property
    def _rules_locals(self):
        task = self.shared('primary_task')

        def match(pattern, value):
            return re.match(pattern, value) is not None

        variables = {
            # constants
            'BREW_TASK_ID': task.task_id,
            # build target *can* be None...
            'BREW_TASK_TARGET': task.target if task.target is not None else '',
            'BREW_TASK_ISSUER': task.issuer,
            'NVR': task.nvr,
            'SCRATCH': task.scratch,

            # functions
            'MATCH': match
        }

        log_dict(self.debug, 'locals', variables)

        return variables

    @cached_property
    def _rules_globals(self):
        # pylint: disable=no-self-use

        return {
            '__builtins__': None
        }

    def _reduce_section(self, commands, is_component=True, default_commands=None, all_commands=None):
        # pylint: disable=too-many-statements
        """
        Reduce commands to a minimal set - apply filtering rules, apply global sections,
        and return set of command sets.
        """

        self.debug('reduce section:\n{}'.format(format_dict(commands)))

        all_commands = all_commands or []
        default_commands = default_commands or []

        reduced = {}

        def _default_flags():
            return {
                'apply-all': None,
                'recipients': None,
                'options': None
            }

        section_flags = _default_flags()

        def _add_command_set(name, set_commands):
            self.debug("    adding command set '{}', with commands:\n{}".format(name, format_dict(set_commands)))

            if not set_commands:
                if is_component is True:
                    # there is nothing in this command set, not even flag telling us
                    # to avoid "all" commands, therefore add just them
                    self.debug('      empty command set, using only "all" commands')
                    reduced[name] = all_commands[:]

                else:
                    # command sets in global sections are simply empty
                    self.debug('      empty command set')
                    reduced[name] = []

                return

            # cannot use section_flags.copy() because section_flags might be an ordered dict,
            # and copy into unordered leads to an exception - we don't care about ordering,
            # we can ignore it.
            set_flags = {key: value for key, value in section_flags.iteritems()}

            if is_component is True:
                if isinstance(set_commands[0], dict):
                    log_dict(self.debug, 'set flags', set_commands[0])

                    set_flags.update(set_commands[0])
                    del set_commands[0]

                for flag in set_flags.iterkeys():
                    if flag in TestBatchPlanner.KNOWN_FLAGS:
                        continue

                    self.warn("Flag '{}' is not supported (typo maybe?)".format(flag), sentry=True)

                self.debug('final set flags:\n{}'.format(format_dict(set_flags)))

                if set_flags['options']:
                    options = set_flags['options']

                    self.debug('set-wide options set to:\n{}'.format(format_dict(options)))

                    for i, command in enumerate(set_commands):
                        self.debug('adding set-wide options to command: {}'.format(command))

                        command = '{} {}'.format(command, options)

                        self.debug('with set options applied: {}'.format(command))

                        set_commands[i] = command

                if set_flags.get('apply-all', True) is not False:
                    self.debug("      allows 'all' section to be appended")
                    set_commands = set_commands[:] + all_commands

            if set_flags.get('recipients', None) is not None:
                self.debug('set-wide recipients set to: {}'.format(set_flags['recipients']))

                recipients = ','.join([s.strip() for s in set_flags['recipients'].split(',')])

                for i, command in enumerate(set_commands):
                    command = command.strip()

                    self.debug("command: '{}'".format(command))

                    for job, result_type in self.job_result_types.iteritems():
                        if not command.startswith(job):
                            continue

                        command = '{} --notify-recipients-options="--{}-add-notify {}"'.format(command, result_type,
                                                                                               recipients)
                        self.debug("with set recipients applied: '{}'".format(command))

                        set_commands[i] = command
                        break

                    else:
                        self.warn("Cannot add recipients to '{}' pipeline".format(command), sentry=True)

            reduced[name] = set_commands[:]

        if commands is None:
            # No tests in this section
            self.debug('  section contains no commands')

            if is_component is True:
                return {
                    'default': default_commands[:]
                }

            return {
                'default': []
            }

        if isinstance(commands, list):
            # foo:
            #   - flag1: foo
            #     flag2: bar
            #   - command1
            #   - command2
            #
            # Simply add commands as a "default" set.

            _add_command_set('default', commands)
            return reduced

        if isinstance(commands, dict):
            # Now it gets complicated:
            #
            # foo:
            #   extra-testing:
            #     - rules
            #     - command1
            #     - command2
            #   extra-special-testing:
            #     - rules
            #     - flag1: foo
            #       flag2: bar
            #     - command3
            #     - command4
            #   default:
            #     - command5

            if 'flags' in commands:
                section_flags = commands['flags']
                del commands['flags']

            else:
                section_flags = _default_flags()

            log_dict(self.debug, 'section flags', section_flags)

            for set_name, set_commands in commands.iteritems():
                self.debug('  checking command set {}'.format(set_name))

                if set_name == 'default':
                    # no rules
                    _add_command_set('default', set_commands)
                    continue

                if set_commands is None or len(set_commands) < 2:
                    raise NoFilteringRulesError(set_name, set_commands)

                rules = Rules(set_commands[0])
                self.debug('    evaluating rules {}'.format(rules))

                if rules.eval(self._rules_globals, self._rules_locals) is not True:
                    self.debug('    denied by rules')
                    continue

                del set_commands[0]

                self.debug('    allowed by rules')
                _add_command_set(set_name, set_commands)

            if 'default' in reduced and len(reduced) > 1:
                self.debug('  there are other sections, not just "default" - remove it')
                del reduced['default']

            return reduced

        raise UnexpectedConfigDataError(commands)

    def _construct_command_sets(self, config, component):
        """
        Preprocess configuration for given component, and create a pile of
        "command sets". Each set has a name and list of commands, and can carry
        filtering rules.

        Returns a dictionary, where keys are set names and values are list of commands.

        .. code-block:: python

           {
               'default': [cmd1, cmd2],
               'foo': [cmd3, cmd4]
           }

        Commands listed in "all" section of the config file are added to every command
        set.

        Commands listed in "default" section of the config file are used when there is
        not specific configuration for the component.

        :param str component: component name.
        """

        self.debug("construct command sets for component '{}'".format(component))

        def _reduce_global_section(name):
            self.debug('reducing "{}" section'.format(name))

            commands = self._reduce_section(config.get(name, []), is_component=False)

            if commands is None:
                self.debug('  empty section, empty list')

                return []

            if len(commands) > 1:
                raise CIError('Top-level section {} must reduce to a single command set'.format(name))

            self.debug('reduced to:\n{}'.format(format_dict(commands)))
            return commands.values()[0]

        global_all_commands = _reduce_global_section('all')
        self.debug('global "all" commands:\n{}'.format(format_dict(global_all_commands)))

        global_default_commands = _reduce_global_section('default')
        self.debug('global "default" commands:\n{}'.format(format_dict(global_default_commands)))

        packages_config = config.get('packages', None)
        if packages_config is None:
            # either there's no key "packages", or it's empty
            packages_config = {}

        component_commands = None

        for pattern, commands in packages_config.iteritems():
            self.debug("pattern: '{}'".format(pattern))

            try:
                if not re.match('^' + pattern + '$', component):
                    continue

            except re.error as exc:
                raise CIError("Cannot compile regexp pattern '{}': {}".format(pattern, str(exc)))

            if component_commands:
                raise CIError("Multiple patterns matching component name '{}'".format(component))

            self.debug('  match!')
            component_commands = commands

        return self._reduce_section(component_commands,
                                    all_commands=global_all_commands,
                                    default_commands=global_default_commands)

    def _plan_by_static_config(self):
        config = load_yaml(self.option('config'), logger=self.logger)

        if config is None:
            self.warn('Empty dispatcher configuration')
            self.config = {}

        task = self.shared('primary_task')

        self.debug('find out which config section we should use')

        matching_config = None

        for section in config:
            if 'rule' not in section:
                self.warn("Section does not contain 'rule' key, ignored", sentry=True)
                continue

            rules = Rules(section['rule'])
            self.debug('evaluating rules {}'.format(rules))

            if rules.eval(self._rules_globals, self._rules_locals) is not True:
                self.debug('denied by rules')
                continue

            matching_config = section
            break

        else:
            self.warn('Cannot select any section, no rules matched current environment')
            return

        # Find command sets for the component
        commands = self._construct_command_sets(matching_config, task.component)
        log_dict(self.debug, 'commands', commands)

        final_commands = []

        for set_name, set_commands in commands.iteritems():
            commands_desc = '\n'.join(['  {}'.format(command) for command in set_commands])
            self.info("Set '{}':\n{}".format(set_name, commands_desc))

            for command in set_commands:
                module = shlex.split(command)[0]
                args = shlex.split(command)[1:]

                self.debug("module='{}', args='{}'".format(module, args))
                final_commands.append((module, args))

        return final_commands

    def plan_test_batch(self):
        """
        Returns list of modules and their options. These modules implement testing process
        of given artifact.

        Return this kind of structure:

        .. code-block:: python

           [
               [ module1, [--option1, --option2, ...] ],
               [ module2, [--option3, --option4, ...] ],
               ...
           ]

        :rtype: list(list)
        """

        self.require_shared('primary_task')

        for method in self._methods:
            self.debug("Plan test batch using '{}' method".format(method))

            test_batch = self._planners[method]()

            if not test_batch:
                self.info("Method '{}' provided no tests, moving on".format(method))
                continue

            return test_batch

        return []

    def sanity(self):
        self._planners = {
            'static-config': self._plan_by_static_config
        }

        methods = self.option('methods')

        if isinstance(methods, (str, unicode)):
            methods = [method for method in methods.split(',')]

        self._methods = [method.strip() for method in methods]

        for method in self._methods:
            if method not in self._planners:
                raise CIError("Unknown method '{}'".format(method))

            if method == 'static-config' and not self.option('config'):
                raise libci.utils.IncompatibleOptionsError(self,
                                                           "--config option is required with method 'static-config'")
