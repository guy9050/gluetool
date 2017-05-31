import os
import re
import shlex
import ast
import _ast
import yaml

from libci import Module, CIError, SoftCIError
from libci.utils import format_dict, cached_property


class RulesError(SoftCIError):
    """
    Base class of rules-related soft exceptions.

    :param str message: descriptive message, passed to parent Exception classes.
    :param Rules rules: rules in question.
    :param str intro: introductory text, pasted at the beginning of template.
    :param str error: specific error message.
    """

    SUBJECT = 'Unable to parse filtering rules'
    BODY = """
{intro}:

    {rules}

    {error}

Please, review the configuration of your component, for complete information on filtering rules
and supported operators and functions see documentation of `brew-dispatcher` module ([1]).

[1] https://url.corp.redhat.com/dafcf63
    """

    def __init__(self, message, rules, intro, error):
        super(RulesError, self).__init__(message)

        self.rules = rules
        self.intro = intro
        self.error = error

    def _template_variables(self):
        variables = super(RulesError, self)._template_variables()

        variables.update({
            'rules': str(self.rules),
            'intro': self.intro,
            'error': self.error
        })

        return variables


class CommandsError(SoftCIError):
    """
    Base class of commands-related soft exceptions.

    :param str message: descriptive message, passed to parent Exception classes.
    :param obj commands: commands in question. Will be formatted and pasted into
      the template.
    """

    SUBJECT = 'Invalid component configuration'
    BODY = """
{message}:

{commands}

Please, review the configuration of your component, for documentation on how to
enable CI for a component and how to configure it see our How To page ([1]).

[1] https://wiki.test.redhat.com/BaseOs/Projects/CI/Documentation/UserHOWTO#AddthecomponenttoCI
    """

    def __init__(self, message, commands):
        super(CommandsError, self).__init__(message)

        self.commands = commands

    def _template_variables(self):
        variables = super(CommandsError, self)._template_variables()

        variables.update({
            'commands': format_dict(self.commands)
        })

        return variables


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
            'Unexpected command or structures foudn in config file',
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
            return compile(tree, '<brew-dispatcher.yaml>', 'eval')

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


class CIBrewDispatcher(Module):
    """
    A configurable dispatcher for Brew builds.

    Possible config file formats are::

        component:
            - command1
            - command2

    The basic form, will dispatch `command1` and `command2` for every build of `component`.

    Another is a bit more complicated::

        component:
            extra-testing:
                - BREW_TASK_TARGET == 'rhel-7.4-candidate'
                - flag1: foo
                  flag2: bar
                - command1
                - command2
            extra-special-testing:
                - BREW_TASK_TARGET == 'rhel-6.5-z-candidate'
                - command3
                - command4
            default:
                - command5

    This allows more fine-grained filtering of commands. This way, when brew build's target
    is `'rhel-7.4-candidate'`, `command1` and `command2` will be dispatched, `command3` and
    `command4` will be dispatched when target is `'rhel-6.5-z-candidate'`, and for every
    other build, `command5` will run.

    Few notes related to the second form:

      - filtering rules *must* be the first entry in the set,
      - flags are optional - the come right after rules, but can be omited
      - "default" set is optional - if you don't specify it, no harm to the configuration
        you just don't have any "fallback" section for cases when no filter rules matched,
      - if you use one name of the set multiple times, the last will overwrite all former
        ones.

    In both forms, ``component`` is being considered a regular expression, and the actual
    component name is then matched against these patterns to find out which section of
    the config file belongs to the current component.

    There are two global sections: "default" which applies for components that don't have
    their own configuration, and "all" which is appended to commands for every component.
    Both sections can use filtering to better specify commands.

    The only supported fag so far is "apply-all" (default: True). When set to False,
    commands from "all" section will not be appended to component's commands. This is useful
    in case you want to "opt out" from global configuration, e.g. you wish to run rpmdiff
    for your component but with different options.

    Filtering rules are Python expressions, limited in offering of available operations:

      Variables::

        BREW_TASK_ID
        BREW_TASK_TARGET
        BREW_TASK_ISSUER
        NVR
        SCRATCH (True if task was a scratch build)

      Types::

        str, int, float

      Operators::

        ==, !=, <, <=, >, >=, is, is not, in, not in
        and, or, not

      Functions::

        MATCH(pattern, variable)
    """

    # Supported flags - keep them alphabetically sorted
    KNOWN_FLAGS = ('apply-all', 'recipients')

    name = 'brew-dispatcher'
    description = 'Configurable brew dispatcher'

    python_requires = 'PyYAML'
    build = dict()
    config = None

    options = {
        'config': {
            'help': 'BaseOS dispatcher configuration'
        },
        'id': {
            'help': 'Brew task id',
        },
        'name': {
            'help': 'Package name',
        },
        # 'list': {
        #    'help': 'List dispatcher configuration',
        # },
        'job-result-type': {
            'help': 'List of comma-separated pairs <job>:<result type>',
            'action': 'append',
            'default': []
        },
        'release': {
            'help': 'Package release',
        },
        'scratch': {
            'help': 'Scratch build (default: false)',
            'default': False,
        },
        'target': {
            'help': 'Package brew target',
        },
        'version': {
            'help': 'Package version',
        },
        # 'verify': {
        #    'help': 'Verify dispatcher configuration',
        # },
    }

    required_options = ['config']

    def _load_config(self):
        """
        Load dispatcher configuration from a config file.
        """

        config = os.path.expanduser(self.option('config'))

        # check if configuration exists
        if not os.path.exists(config):
            raise CIError('file \'{}\' does not exist'.format(config))

        # read yaml configuration
        try:
            with open(config, 'r') as stream:
                self.config = yaml.load(stream)

        except yaml.YAMLError as e:
            raise CIError('Unable to load configuration: {}'.format(str(e)))

        if self.config is None:
            self.warn('Empty dispatcher configuration')
            self.config = {}

        self.debug('config:\n{}'.format(format_dict(self.config)))

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
        task = self.shared('brew_task')

        def match(pattern, value):
            return re.match(pattern, value) is not None

        variables = {
            # constants
            'BREW_TASK_ID': task.task_id,
            'BREW_TASK_TARGET': task.target.target,
            'BREW_TASK_ISSUER': task.issuer,
            'NVR': task.nvr,
            'SCRATCH': task.scratch is True,

            # functions
            'MATCH': match
        }

        self.debug('locals:\n{}'.format(format_dict(variables)))

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

            flags = {
                'apply-all': True,
                'recipients': None
            }

            if is_component is True:
                if isinstance(set_commands[0], dict):
                    self.debug('      specifies flags:\n{}'.format(format_dict(set_commands[0])))

                    flags.update(set_commands[0])
                    del set_commands[0]

                for flag in [flag for flag in flags.iterkeys() if flag not in CIBrewDispatcher.KNOWN_FLAGS]:
                    self.warn("Flag '{}' is not supported (typo maybe?)".format(flag), sentry=True)

                self.debug('      final flags:\n{}'.format(format_dict(flags)))

                if flags['apply-all'] is True:
                    self.debug("      allows 'all' section to be appended")
                    set_commands = set_commands[:] + all_commands

            if flags['recipients']:
                self.debug('set-wide recipients set to: {}'.format(flags['recipients']))

                recipients = ','.join([s.strip() for s in flags['recipients'].split(',')])

                for i, command in enumerate(set_commands):
                    splitted = shlex.split(command)
                    self.debug('splitted command: {}'.format(splitted))

                    # [wow-job, --option1, --option2, ...]
                    command_module = splitted[0]
                    if command_module not in self.job_result_types:
                        msg = 'Cannot add recipients to {} pipeline'.format(command_module)

                        self.warn(msg)
                        self.ci.sentry_submit_warning(msg)

                        continue

                    result_type = self.job_result_types[splitted[0]]

                    splitted.append("--notify-recipients-options='--{}-add-notify {}'".format(result_type, recipients))
                    self.debug('with set recipients applied: {}'.format(format_dict(splitted)))

                    set_commands[i] = ' '.join(splitted)

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

        Commands listed in 'default' section of the config file - the top-level one,
        not the component-specific! - are added to every command set. This means that
        when there are no tests in config file for the component, then caller will
        get the "default" set, with commands from "default" section.

        When component does not use any filtering rules (see help of this module),
        it's commands simply constitute "default" section. The "filtering" form
        must explicitly specify "default" section when user wants to dispatch
        commands for other builds as well.

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

    def verify(self):
        pass

    def sanity(self):
        # parse configuration
        self._load_config()

        # set options from command line or environment
        for option in ['name', 'version', 'release', 'target', 'id']:
            try:
                self.build[option] = os.environ[option]
            except KeyError:
                # for cmdline options replace '_' with '-'
                if not self.option(option):
                    raise CIError("Required option '{}' not found in the environment or command line".format(option))
                self.build[option] = self.option(option)

    def _dispatch_tests(self):
        """
        Dispatch tests for a component. That means we have to get command sets for the component,
        check which of them apply, given their rules and current environment, and then dispatch
        the commands we have left.
        """

        component = self.build['name']

        task = self.shared('brew_task')
        if task is None:
            raise CIError('Need a brew task to continue')

        self.debug('find out which config section we should use')

        matching_config = None

        for section in self.config:
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
        commands = self._construct_command_sets(matching_config, component)
        self.debug('commands:\n{}'.format(format_dict(commands)))

        def _dispatch_commands(commands):
            """
            Dispatch commands, one by one. This usually leads to starting some
            Jenkins jobs, as specified in config file, and passing them necessary
            parameters.
            """

            for command in commands:
                module = shlex.split(command)[0]
                args = shlex.split(command)[1:]

                self.debug("module='{}', args='{}'".format(module, args))
                self.run_module(module, args)

        self.info('Dispatching command sets')

        for set_name, set_commands in commands.iteritems():
            commands_desc = '\n'.join(['  {}'.format(command) for command in set_commands])
            self.info("Set '{}':\n{}".format(set_name, commands_desc))

            _dispatch_commands(set_commands)

    def execute(self):
        self._dispatch_tests()
        # self.info("package '{}' not enabled for target '{}'".format(self.build['name'], self.build['target']))
