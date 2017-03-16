import os
import re
import shlex
import ast
import _ast
import yaml

from libci import Module, CIError
from libci.utils import format_dict, cached_property


class SanityASTVisitor(ast.NodeVisitor):
    """
    Custom AST visitor. It's only purpose is to visit every node in the tree,
    and verify that there's no disallowed node. We don't want to allow stuff
    like calling functions, and limit rules to basic expressions.
    """

    _valid_classes = tuple([
        getattr(_ast, node_class) for node_class in (
            'Expression', 'Expr', 'Compare', 'Name', 'Load',
            'Str', 'Num',
            'Eq', 'NotEq', 'Lt', 'LtE', 'Gt', 'GtE', 'Is', 'IsNot', 'In', 'NotIn',
            'And', 'Or', 'Not',
            'Call'
        )
    ])

    _valid_functions = ('MATCH',)

    def generic_visit(self, node):
        if not isinstance(node, SanityASTVisitor._valid_classes):
            raise CIError('Node of type {} not allowed in rules'.format(node.__class__.__name__), soft=True)

        if isinstance(node, _ast.Call) and node.func.id not in SanityASTVisitor._valid_functions:
            raise CIError('Calling function {} not allowed in rules'.format(node.func.id), soft=True)

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
            raise CIError("Cannot parse rules '{}': line {}, offset {}".format(self._rules, e.lineno, e.offset),
                          soft=True)

        SanityASTVisitor().visit(tree)

        try:
            return compile(tree, '<brew-dispatcher.yaml>', 'eval')

        except Exception as e:
            raise CIError("Cannot evaluate rules '{}': {}".format(self._rules, str(e)), soft=True)

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

    Possible config file formats are:

        component:
            - command1
            - command2

    The basic form, will dispatch `command1` and `command2` for every build of `component`.


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

    There are two global sections: "default" which applies for components that don't have
    their own configuration, and "all" which is appended to commands for every component.
    Both sections can use filtering to better specify commands.

    The only supported fag so far is "apply-all" (default: True). When set to False,
    commands from "all" section will not be appended to component's commands. This is useful
    in case you want to "opt out" from global configuration, e.g. you wish to run rpmdiff
    for your component but with different options.

    Filtering rules are Python expressions, limited in offering of available operations:

      Variables:
        BREW_TASK_ID
        BREW_TASK_TARGET
        BREW_TASK_ISSUER
        NVR
        SCRATCH (True if task was a scratch build)

      Types:
        str, int, float

      Operators:
        ==, !=, <, <=, >, >=, is, is not, in, not in
        and, or, not

      Functions:
        MATCH(pattern, variable)
    """

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
        with open(config, 'r') as stream:
            self.config = yaml.load(stream)

        self.debug('config:\n{}'.format(format_dict(self.config)))

    @cached_property
    def _rules_locals(self):
        task = self.shared('brew_task')

        def match(pattern, value):
            return re.match(pattern, value) is not None

        return {
            # constants
            'BREW_TASK_ID': task.task_id,
            'BREW_TASK_TARGET': task.target.target,
            'BREW_TASK_ISSUER': task.owner,
            'NVR': task.nvr,
            'SCRATCH': task.scratch is True,

            # functions
            'MATCH': match
        }

    @cached_property
    def _rules_globals(self):
        # pylint: disable=no-self-use

        return {
            '__builtins__': None
        }

    def _reduce_section(self, commands, is_component=True, default_commands=None, all_commands=None):
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

            if is_component is True:
                flags = {
                    'apply-all': True
                }

                if isinstance(set_commands[0], dict):
                    self.debug('      specifies flags:\n{}'.format(format_dict(set_commands[0])))

                    flags.update(set_commands[0])
                    del set_commands[0]

                self.debug('      final flags:\n{}'.format(format_dict(flags)))

                if flags['apply-all'] is True:
                    self.debug("      allows 'all' section to be appended")
                    set_commands = set_commands[:] + all_commands

            reduced[name] = set_commands[:]

        if commands is None:
            # No tests in this section
            self.debug('  section contains no commands')

            if is_component is True:
                return {
                    'default': default_commands[:]
                }

            else:
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

                if len(set_commands) < 2:
                    raise CIError("Command set '{}' does not contain filtering rules".format(set_name), soft=True)

                rules = Rules(set_commands[0])
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

        raise CIError('Unexpected data found in config', soft=True)

    def _construct_command_sets(self, component):
        """
        Preprocess configuration for given component, and create a pile of
        "command sets". Each set has a name and list of commands, and can carry
        filtering rules.

        Returns a dictionary, where keys are set names and values are list of commands.

            {
                'default': [cmd1, cmd2],
                'foo': [cmd3, cmd4],
                ...
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

            commands = self._reduce_section(self.config.get(name, []), is_component=False)

            if commands is None:
                self.debug('  empty section, empty list')

                return []

            if len(commands) > 1:
                raise CIError('Top-level section {} must reduce to a single command set'.format(name))

            self.debug('reduced to:\n{}'.format(format_dict(commands)))
            return commands.values()[0]

        global_all_commands = _reduce_global_section('all')
        self.debug('global "all" commands:\n{}'.format(global_all_commands))

        global_default_commands = _reduce_global_section('default')
        self.debug('global "default" commands:\n{}'.format(global_default_commands))

        packages_config = self.config.get('packages', None)
        if packages_config is None:
            # either there's no key "packages", or it's empty
            packages_config = {}

        return self._reduce_section(packages_config.get(component, None),
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

        # Find command sets for the component
        commands = self._construct_command_sets(component)
        self.debug('commands:\n{}'.format(format_dict(commands)))

        def _dispatch_commands(commands):
            """
            Dispatch commands, one by one. This usually leads to starting some
            Jenkins jobs, as specified in config file, and passing them necessary
            parameters.
            """

            for command in commands:
                self.info("dispatching command '{}'".format(command))

                module = shlex.split(command)[0]
                args = shlex.split(command)[1:]

                self.debug("module='{}', args='{}'".format(module, args))
                self.run_module(module, args)

        self.info('Dispatching command sets')

        for set_name, set_commands in commands.iteritems():
            self.debug("set '{}':\n{}".format(set_name, format_dict(set_commands)))

            # Ignore default section, that's just a fallback
            if set_name == 'default':
                continue

            _dispatch_commands(set_commands)

    def execute(self):
        self._dispatch_tests()
        # self.info("package '{}' not enabled for target '{}'".format(self.build['name'], self.build['target']))
