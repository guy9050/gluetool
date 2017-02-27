import os
import shlex
import ast
import _ast
import yaml

from libci import Module, CIError
from libci.utils import format_dict


class SanityASTVisitor(ast.NodeVisitor):
    """
    Custom AST visitor. It's only purpose is to visit every node in the tree,
    and verify that there's no disallowed node. We don't want to allow stuff
    like calling functions, and limit rules to basic expressions.
    """

    _valid_classes = [
        getattr(_ast, node_class) for node_class in (
            'Expression', 'Expr', 'Compare', 'Name', 'Load',
            'Str', 'Num',
            'Eq', 'NotEq', 'Lt', 'LtE', 'Gt', 'GtE', 'Is', 'IsNot', 'In', 'NotIn',
            'And', 'Or', 'Not'
        )
    ]

    def generic_visit(self, node):
        if not isinstance(node, SanityASTVisitor._valid_classes):
            raise CIError('Node of type {} not allowed in rules'.format(node.__class__.__name__), soft=True)

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
      - "default" set is optional - if you don't specify it, no harm to the configuration
        you just don't have any "fallback" section for cases when no filter rules matched,
      - if you use one name of the set multiple times, the last will overwrite all former
        ones.

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

    def _construct_command_sets(self, component):
        """
        Preprocess configuration for given component, and create a pile of
        "command sets". Each set has a name and list of commands, and can carry
        filtering rules.

        Returns a dictionary, where keys are set names and values are tuples, with
        the first item being rules (or None), and the second item is a list of
        commands.

            {
                'default': (None, [cmd1, cmd2]),
                'foo': (Rules('BREW_TASK_TARGET == rhel-7.4-candidate'), [cmd3, cmd4]),
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

        global_always_commands = self.config.get('default', [])
        if global_always_commands is None:
            global_always_commands = []
        self.debug('global "always" commands:\n{}'.format(format_dict(global_always_commands)))

        commands = {}

        config_commands = self.config.get('packages', {}).get(component, None)
        self.debug('commands in config file:\n{}'.format(format_dict(config_commands)))

        def _add_command_set(name, set_commands, rules=None):
            commands[name] = (rules, global_always_commands + set_commands)

        if config_commands is None:
            # No tests for this component

            _add_command_set('default', [])
            return commands

        if isinstance(config_commands, list):
            # this is how "normal" components look like:
            #
            # foo:
            #   - command1
            #   - command2
            #
            # Simply add commands as a "default" set.

            _add_command_set('default', config_commands)
            return commands

        if isinstance(config_commands, dict):
            # Now it gets complicated:
            #
            # foo:
            #   extra-testing:
            #     - rules
            #     - command1
            #     - command2
            #   extra-special-testing:
            #     - rules
            #     - command3
            #     - command4
            #   default:
            #     - command5
            #
            # However it's quite simple at the end - we get this as a dictionary, and simply
            # convert each key (default, extra-testing, extra-special-testing) to a set in
            # components dinctionary.

            for set_name, set_commands in config_commands.iteritems():
                if set_name == 'default':
                    # no rules
                    _add_command_set('default', set_commands)
                    continue

                if len(set_commands) < 2:
                    raise CIError("Set '{}' of component '{}' must contain filtering rule".format(set_name, component),
                                  soft=True)

                rules = Rules(set_commands[0])
                _add_command_set(set_name, set_commands[1:], rules=rules)

            return commands

        raise CIError("Unexpected data found in config for component '{}'".format(component), soft=True)

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

        # If we dispatch commands from at least one set, we don't have to fall back to "default" set
        dispatched = 0

        # Context for rule evaluation
        our_locals = {
            'BREW_TASK_ID': task.task_id,
            'BREW_TASK_TARGET': task.target.target,
            'BREW_TASK_ISSUER': task.owner,
            'NVR': task.nvr,
            'SCRATCH': task.scratch is True
        }

        our_globals = {
            '__builtins__': None
        }

        self.debug('locals:\n{}'.format(format_dict(our_locals)))

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

        for set_name, (rules, set_commands) in commands.iteritems():
            self.debug("set '{}': rules '{}', commands '{}'".format(set_name, rules, set_commands))

            # Ignore default section, that's just a fallback
            if set_name == 'default':
                continue

            # Check rules
            if rules.eval(our_globals, our_locals) is not True:
                self.debug('denied by rules')
                continue

            self.debug('allowed by rules')

            # Yay, dispatch \o/
            _dispatch_commands(set_commands)
            dispatched += 1

        if dispatched != 0:
            self.debug("already dispatched {} sets of commands, skip 'default'".format(dispatched))
            return

        # Dispatch "default" set, if there's any
        if 'default' in commands:
            _dispatch_commands(commands['default'][1])

    def execute(self):
        self._dispatch_tests()
        # self.info("package '{}' not enabled for target '{}'".format(self.build['name'], self.build['target']))
