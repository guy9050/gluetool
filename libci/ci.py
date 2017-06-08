import argparse
import ConfigParser
import imp
import logging
import os
import sys
import ast

from .help import LineWrapRawTextHelpFormatter, option_help, docstring_to_help
from .log import Logging, ContextAdapter, ModuleAdapter


CONFIGS = ['/etc/citool.d/citool', os.path.expanduser('~/.citool.d/citool')]
MODULE_CONFIG_PATHS = ['/etc/citool.d/config',
                       os.path.expanduser('~/.citool.d/config')]
MODULE_PATH = [os.path.dirname(os.path.abspath(__file__)) + '/modules']
DATA_PATH = os.path.dirname(os.path.abspath(__file__)) + '/data'


class CIError(Exception):
    """
    Generic ``libci`` exception.
    """

    pass


class SoftCIError(CIError):
    """
    **Soft** errors are errors CI Ops and/or developers shouldn't be bothered with, things that
    are up to the user to fix, e.g. empty set of tests. **Hard** errors are supposed to warn Ops/Devel
    teams about important infrastructure issues, code deficiencies, bugs and other issues that are
    fixable only by actions of CI staff.

    However, we still must provide notification to user(s), and since we expect them to fix the issues
    that led to raising the soft error, we must provide them with as much information as possible.
    Therefore soft errors contain a template that can be used to format the error into a descriptive
    text, usable e.g. in e-mail.
    """

    BODY = None
    BODY_HEADER = None
    BODY_FOOTER = None
    MODULE_NAME = None
    STATUS = 'ABORT'
    SUBJECT = None

    def __init__(self, *args, **kwargs):
        assert self.STATUS is not None
        assert self.SUBJECT is not None
        assert self.BODY is not None

        super(SoftCIError, self).__init__(*args, **kwargs)

    def _template_variables(self):
        """
        Override this method to provide more variables for rendering exception template.
        """

        return {
            'message': self.message
        }

    def render(self):
        """
        Render template with variables provided by the exception instance.
        """

        from .utils import render_template

        variables = self._template_variables()

        return {
            'subject': render_template(self.SUBJECT, **variables),
            'body': render_template(self.BODY, **variables)
        }


class CIRetryError(CIError):
    """ Retry libci exception """
    pass


class CICommandError(CIError):
    """
    Exception raised when external command failes.

    :param list cmd: Command as passed to libci.utils.run_command helper.
    :param libci.utils.ProcessOutput output: Process output data.

    :ivar list cmd: Command as passed to libci.utils.run_command helper.
    :ivar libci.utils.ProcessOutput output: Process output data.
    """

    def __init__(self, cmd, output):
        super(CICommandError, self).__init__("Command '{}' failed with exit code {}".format(cmd, output.exit_code))

        self.cmd = cmd
        self.output = output


class Failure(object):
    # pylint: disable=too-few-public-methods

    """
    Bundles exception related info. Used to inform modules in their ``destroy()`` phase
    that ``citool`` session was killed because of exception raised by one of modules.

    :param libci.ci.Module module: module in which the error happened, or ``None``.
    :param tuple exc_info: Exception information as returned by :py:func:`sys.exc_info`.

    :ivar libci.ci.Module module: module in which the error happened, or ``None``.
    :ivar tuple exc_info: Exception information as returned by :py:func:`sys.exc_info`.
    """

    def __init__(self, module, exc_info):
        self.module = module
        self.exc_info = exc_info

        if exc_info:
            exc = exc_info[1]

            self.soft = isinstance(exc, SoftCIError)

        else:
            self.soft = False


def retry(*args):
    """ Retry decorator
    This decorator catches given exceptions and returns
    libRetryError exception instead.

    usage: @retry(exception1, exception2, ..)
    """
    def wrap(func):
        def func_wrapper(obj, *fargs, **fkwargs):
            try:
                func(obj, *fargs, **fkwargs)
            except args as e:
                if isinstance(e, CIError):
                    raise CIRetryError(e.value)
                else:
                    raise CIRetryError(e)
        return func_wrapper
    return wrap


class Configurable(object):
    """
    Base class of two main ``citool`` classes - :py:class:`libci.ci.CI` and :py:class:`libci.ci.Module`.
    Gives them the ability to use `options`, settable from configuration files and/or command-line arguments.

    :ivar dict _config: internal configuration store. Values of all options
      are stored here, regardless of them being set on command-line or by the
      configuration file.
    """

    options = {}
    """
    The ``options`` variable defines options accepted by module, and their properties::

        options = {
            <option name>: {
                <option properties>
            },
        }

    where

    * ``<option name>`` is used to `name` the option in the parser, and two formats are accepted (don't
      add any leading dashes (``-`` nor ``--``):

      * ``<long name>``
      * ``tuple(<short name>, <long name>)``

    * dictionary ``<option properties>`` is passed to :py:meth:`argparse.ArgumentParser.add_argument` as
      keyword arguments when the option is being added to the parser, therefore any arguments recognized
      by :py:mod:`argparse` can be used.
    """

    required_options = []
    """Iterable of names of required options."""

    def __init__(self):
        super(Configurable, self).__init__()

        # Initialize configuration store
        self._config = {}

        # Initialize values in the store, and make sanity check of option names
        def _fail_name(name):
            raise CIError("Option name must be either a string or (<letter>, <string>), '{}' found".format(name))

        for name, params in self.options.iteritems():
            if isinstance(name, str):
                if not isinstance(name, str):
                    _fail_name(name)

                self._config[name] = None

            elif isinstance(name, tuple):
                if not isinstance(name[0], str) or len(name[0]) != 1:
                    _fail_name(name)

                if not isinstance(name[1], str) or len(name[1]) < 2:
                    _fail_name(name)

                self._config[name[1]] = None

            else:
                _fail_name(name)

            if 'help' not in params:
                continue

            # Long help texts can be written using triple quotes and docstring-like
            # formatting. Convert every help string to a single line string.
            params['help'] = option_help(params['help'])

    def _parse_config(self, paths):
        """
        Parse configuration files. Uses :py:mod:`ConfigParser` for the actual parsing.
        Updates module's configuration store with values found returned by the parser.

        :param list paths: List of paths to possible configuration files.
        """

        from .utils import format_dict

        self.debug('Loading configuration from following paths:\n{}'.format(format_dict(paths)))

        parser = ConfigParser.ConfigParser()
        parser.read(paths)

        for name in self.options:
            if isinstance(name, tuple):
                name = name[1]

            try:
                value = parser.get('default', name)

            except (ConfigParser.NoOptionError, ConfigParser.NoSectionError):
                continue

            self._config[name] = value
            self.debug("Option '{}' set to '{}' by config file".format(name, value))

    @classmethod
    def _create_args_parser(cls, **kwargs):
        """
        Create an argument parser. Used by Sphinx to document "command-line" options
        of the module - which are, by the way, the module options as well.

        :param dict kwargs: Additional arguments passed to :py:class:`argparse.ArgumentParser`.
        """

        parser = argparse.ArgumentParser(**kwargs)

        for name in sorted(cls.options):
            if isinstance(name, str):
                names = ('--{}'.format(name),)

            else:
                names = ('-{}'.format(name[0]), '--{}'.format(name[1]))

            parser.add_argument(*names, **cls.options[name])

        return parser

    def _parse_args(self, args, **kwargs):
        """
        Parse command-line arguments. Uses :py:mod:`argparse` for the actual parsing.
        Updates module's configuration store with values returned by parser.

        :param list args: arguments passed to this module. Similar to what :py:data:`sys.argv` provides on
          program level.
        """

        # construct the parser
        parser = self._create_args_parser(**kwargs)

        # parse the added args
        options = parser.parse_args(args)

        # add the parsed args to options
        for name, params in self.options.iteritems():
            if isinstance(name, tuple):
                name = name[1]

            dest = params.get('dest', name.replace('-', '_'))

            value = getattr(options, dest)

            # if the option was not specified, skip it
            if value is None and name in self._config:
                continue

            # do not replace config options with default command line values
            if name in self._config and self._config[name] is not None:
                # if default parameter used
                if 'default' in params and value == params['default']:
                    continue

                # with action store_true, the default is False
                if params.get('action', '') == 'store_true' and value is False:
                    continue

                # with action store_false, the default is True
                if params.get('action', '') == 'store_false' and value is True:
                    continue

            self._config[name] = value
            self.debug("Option '{}' set to '{}' by command-line".format(name, value))

    def parse_config(self):
        """
        Public entry point to configuration parsing. Child classes must implement this
        method, e.g. by calling :py:meth:`libci.ci.Configurable._parse_config` which
        requires list of paths.
        """

        # E.g. self._parse_config(<list of possible configuration files>)

        raise NotImplementedError('Implement this method to enable the actual parsing')

    def parse_args(self, args):
        """
        Public entry point to argument parsing. Child classes must implement this method,
        e.g. by calling :py:meth:`libci.ci.Configurable._parse_args` which makes use
        of additional :py:class:`argparse.ArgumentParser` options.
        """

        # E.g. self._parse_args(args, description=...)

        raise NotImplementedError('Implement this method to enable the actual parsing')

    def check_required_options(self):
        if not self.required_options:
            self.debug('skipping checking of required options')
            return

        for name in self.required_options:
            if name not in self._config or not self._config[name]:
                raise CIError("Missing required '{}' option".format(name))

    def option(self, name):
        """
        Return a value of given option from module's configuration store.

        :param str name: name of requested option.
        :returns: option value or ``None`` when no such option exists.
        """

        try:
            return self._config[name]

        except KeyError:
            return None


class Module(Configurable):
    """
    Base class of all ``citool`` modules.

    :param libci.ci.CI ci: CI instance owning the module.

    :ivar libci.ci.CI ci: CI instance owning the module.
    :ivar dict _config: internal configuration store. Values of all module options
      are stored here, regardless of them being set on command-line or in the
      configuration file.
    """

    name = None
    """Module name. Usually matches the name of the source file, no suffix."""

    description = None
    """Short module description, displayed in ``citool``'s module listing."""

    #: A list of names of module's shared functions
    shared_functions = []
    """Iterable of names of shared functions exported by the module."""

    def __init__(self, ci):
        super(Module, self).__init__()

        # initialize citool
        self.ci = ci

        # initialize logging helpers
        self.logger = ModuleAdapter(ci.logger, self)
        self.logger.connect(self)

        # initialize data path if exists, else it will be None
        dpath = os.path.join(self.ci.option('data_path') or DATA_PATH, self.name)
        self.data_path = dpath if os.path.exists(dpath) else None

    def parse_config(self):
        self._parse_config([os.path.join(c, self.name) for c in MODULE_CONFIG_PATHS])

    def _generate_shared_functions_help(self):
        """
        Generate help for shared functions provided by the module.

        :returns: Formatted help, describing module's shared functions.
        """

        if not self.shared_functions:
            return ''

        from .help import function_help, SHARED_FUNCTIONS_HELP_TEMPLATE

        functions = []

        for name in self.shared_functions:
            if not hasattr(self, name):
                raise CIError("No such shared function '{}' of module '{}'".format(name, self.name))

            functions.append(function_help(getattr(self, name), name=name))

        return SHARED_FUNCTIONS_HELP_TEMPLATE.format(functions='\n'.join(functions))

    def parse_args(self, args):
        self._parse_args(args,
                         usage='{} [options]'.format(self.name),
                         description=docstring_to_help(self.__doc__),
                         epilog=self._generate_shared_functions_help(),
                         formatter_class=LineWrapRawTextHelpFormatter)

    def destroy(self, failure=None):
        # pylint: disable-msg=no-self-use,unused-argument

        """
        Here should go any code that needs to be run on exit, like job cleanup etc.

        :param libci.ci.Failure failure: if set, carries information about failure that made
          ``citool`` to destroy the whole session. Modules might want to take actions based
          on provided information, e.g. send different notifications.
        """

        return None

    def add_shared(self):
        """
        Register module's shared functions with CI, to allow other modules
        to use them.
        """

        for func in self.shared_functions:
            self.ci.add_shared(func, self)

    def del_shared(self, funcname):
        self.ci.del_shared(funcname)

    def has_shared(self, funcname):
        return self.ci.has_shared(funcname)

    def execute(self):
        """
        execute is a required module function
        """
        raise NotImplementedError

    def sanity(self):
        # pylint: disable-msg=no-self-use
        """
        In this method, modules can define additional checks before execution.

        Some examples:

        * Advanced checks on passed options
        * Check for additional requirements (tools, data, etc.)
        """

        return None

    def shared(self, *args, **kwargs):
        return self.ci.shared(*args, **kwargs)

    def run_module(self, module, args=None):
        self.ci.run_module(module, args or [])


class CI(Configurable):
    options = {
        ('c', 'colors'): {
            'help': 'Colorize logging on the terminal',
            'action': 'store_true'
        },
        ('V', 'version'): {
            'help': 'Print version',
            'action': 'store_true'
        },
        ('d', 'debug'): {
            'help': 'Raise terminal output verbosity to DEBUG (the most verbose)',
            'action': 'store_true'
        },
        ('v', 'verbose'): {
            'help': 'Raise terminal output verbosity to VERBOSE (one step below DEBUG)',
            'action': 'store_true'
        },
        ('q', 'quiet'): {
            'help': 'Silence info messages',
            'action': 'store_true'
        },
        ('o', 'output'): {
            'help': 'Output *everything* to given file, with highest verbosity enabled'
        },
        ('i', 'info'): {
            'help': 'Print command-line that would re-run the citool session',
            'action': 'store_true'
        },
        ('l', 'list'): {
            'help': 'List all available modules',
            'action': 'append',
            'nargs': '?',
            'const': True
        },
        'data-path': {
            'help': 'Specify data path'
        },
        'module-path': {
            'help': 'Specify one or more directories with modules (IMPORTANT: works only with configuration file)',
            'metavar': 'DIR',
            'action': 'append'
        },
        ('r', 'retries'): {
            'help': 'Number of retries',
            'type': int,
            'default': 0
        }
    }

    def sentry_submit_exception(self, exc_info, **kwargs):
        """
        Provide modules way to submit exceptions to Sentry. Unhandled exceptions
        are submitted automagically, but they might feel the need to share
        arbitrary issues with the world.

        When Sentry is not enabled (via ``SENTRY_DSN`` env var), this method simply returns
        without sending anything anywhere.

        :param tuple exc_info: Exception info as provided by :py:func:`sys.exc_info` method
          or ``exc_info`` attribute of :py:class:`libci.ci.Failure` class.
        :param dict kwargs: additional arguments that will be passed to Sentry's ``captureException``
          method.
        """

        if self._sentry is None:
            return

        self._sentry.captureException(exc_info=exc_info, **kwargs)

    def sentry_submit_warning(self, msg, **kwargs):
        """
        Provide modules way to submit messages to Sentry. They might feel the need
        to share arbitrary issues - e.g. warning that are not serious enough to kill
        the citool - with the world.

        When Sentry is not enabled (via ``SENTRY_DSN`` env var), this method simply returns
        without sending anything anywhere.

        :param str msg: Message describing the issue.
        :param dict kwargs: additional arguments that will be passed to Sentry's ``captureMessage``
          method.
        """

        if self._sentry is None:
            return

        self._sentry.captureMessage(msg, **kwargs)

    # add a shared function, overwrite if exists
    def add_shared(self, funcname, module):
        """
        Register a shared function. Overwrite previously registered function
        with the same name, if there was any such.

        :param str funcname: Name of the shared function.
        :param libci.ci.Module module: Module instance providing the shared function.
        """

        if not hasattr(module, funcname):
            raise CIError("No such shared function '{}' of module '{}'".format(funcname, module.name))

        self.shared_functions[funcname] = (module, getattr(module, funcname))

    # delete a shared function if exists
    def del_shared(self, funcname):
        if funcname not in self.shared_functions:
            return

        del self.shared_functions[funcname]

    def has_shared(self, funcname):
        return funcname in self.shared_functions

    # call a shared function
    def shared(self, funcname, *args, **kwargs):
        if funcname not in self.shared_functions:
            return None

        return self.shared_functions[funcname][1](*args, **kwargs)

    #
    # Module loading
    #
    def _check_module_file(self, mfile):
        """
        Make sure the file looks like a ``citool`` module:

        - can be processed by Python parser,
        - imports :py:class:`libci.ci.CI` and :py:class:`libci.ci.Module`,
        - contains child class of :py:class:`libci.ci.Module`.

        :param str mfile: path to a file.
        :returns: ``True`` if file contains ``citool`` module, ``False`` otherwise.
        :raises libci.ci.CIError: when it's not possible to finish the check.
        """

        self.debug("check possible module file '{}'".format(mfile))

        try:
            with open(mfile) as f:
                node = ast.parse(f.read())

            # check for libci import
            def imports_libci(item):
                """
                Return ``True`` if item is an ``import`` statement, and imports ``libci``.
                """

                return (item.__class__.__name__ == 'Import' and item.names[0].name == 'libci') \
                    or (item.__class__.__name__ == 'ImportFrom' and item.module == 'libci')

            if not any((imports_libci(item) for item in node.__dict__['body'])):
                self.debug("  no 'import libci' found")
                return False

            # check for libci.Module class definition
            def has_module_class(item):
                """
                Return ``True`` if item is a class definition, and any of the base classes
                is libci.ci.Module.
                """

                if item.__class__.__name__ != 'ClassDef':
                    return False

                for base in item.bases:
                    if (hasattr(base, 'id') and base.id == 'Module') \
                            or (hasattr(base, 'attr') and base.attr == 'Module'):
                        return True

                return False

            if not any((has_module_class(item) for item in node.__dict__['body'])):
                self.debug('  no child of libci.Module found')
                return False

            return True

        # pylint: disable=broad-except
        except Exception as e:
            raise CIError("Unable to check check module file '{}': {}".format(mfile, str(e)))

    def _import_module(self, import_name, filename):
        """
        Attempt to import a Python module from a file.

        :param str import_name: name assigned to the imported module.
        :param str filepath: path to a file.
        :returns: imported Python module.
        :raises libci.ci.CIError: when import failed.
        """

        self.debug("try to import module '{}' from file '{}'".format(import_name, filename))

        try:
            return imp.load_source(import_name, filename)

        # pylint: disable=broad-except
        except Exception as e:
            raise CIError("Unable to import module '{}' from '{}': {}".format(import_name, filename, str(e)))

    def _load_python_module(self, group, module_name, filepath):
        """
        Load Python module from a file, if it contains ``citool`` modules. If the
        file does not look like it contains ``citool`` modules, or when it's not
        possible to import the Python module successfully, method simply warns
        user and ignores the file.

        :param str import_name: name assigned to the imported module.
        :param str filepath: path to a file.
        :returns: loaded Python module.
        :raises libci.ci.CIError: when import failed.
        """

        # Check content of the file, look for CI and Module stuff
        try:
            if not self._check_module_file(filepath):
                return

        except CIError as e:
            self.info("ignoring file '{}': {}".format(module_name, e.message))
            return

        # Try to import file as a Python module
        import_name = 'libci.ci.{}-{}'.format(group, module_name)

        try:
            module = self._import_module(import_name, filepath)

        except CIError as e:
            self.info("ignoring module '{}': {}".format(module_name, e.message))
            return

        return module

    def _load_citool_modules(self, group, module_name, filepath):
        """
        Load ``citool`` modules from a file. Method attempts to import the file
        as a Python module, and then checks its content and adds all `citool`
        modules to internal module registry.

        :param str group: module group.
        :param str module_name: name assigned to the imported Python module.
        :param str filepath: path to a file.
        :rtype: [(module_group, module_class), ...]
        :returns: list of loaded ``citool`` modules
        """

        module = self._load_python_module(group, module_name, filepath)

        loaded_modules = []

        # Look for citool modules in imported stuff, and add them to our module registry
        for name in dir(module):
            cls = getattr(module, name)

            if not isinstance(cls, type) or not issubclass(cls, Module) or cls == Module:
                continue

            if not hasattr(cls, 'name') or not cls.name:
                raise CIError("No name specified by module class '{}' from file '{}'".format(
                    cls.__name__, filepath))

            if cls.name in self.modules:
                raise CIError("Name '{}' of module '{}' from '{}' is a duplicate module name".format(
                    cls.name, cls.__name__, filepath))

            self.debug("found module '{}', group '{}', in module '{}' from '{}'".format(
                cls.name, group, module_name, filepath))

            self.modules[cls.name] = {
                'class': cls,
                'description': cls.description,
                'group': group
            }

            loaded_modules.append((group, cls))

        return loaded_modules

    def _load_module_path(self, ppath):
        """
        Search and load ``citool`` modules from a directory.

        In essence, it scans every file with ``.py`` suffix, and searches for
        classes derived from :py:class:`libci.ci.Module`.

        :param str ppath: directory to search for `citool` modules.
        """

        for root, _, files in os.walk(ppath):
            for filename in sorted(files):
                if not filename.endswith('.py'):
                    continue

                group = root.replace(ppath + '/', '')
                module_name, _ = os.path.splitext(filename)
                module_file = os.path.join(root, filename)

                self._load_citool_modules(group, module_name, module_file)

    def _load_modules(self):
        """
        Load all available `citool` modules.
        """

        ppaths = self.option('module_path') or MODULE_PATH
        self.debug('loading modules from these paths: {}'.format(ppaths))

        for ppath in ppaths:
            self._load_module_path(ppath)

    def __init__(self, sentry=None):
        # Initialize logging methods before doing anything else.
        # Right now, we don't know the desired log level, or if
        # output file is in play, just get simple logger before
        # the actual configuration is known.
        self.logger = ContextAdapter(Logging.create_logger(sentry=sentry))
        self.logger.connect(self)

        super(CI, self).__init__()

        # module types dictionary
        self.modules = {}
        self.module_instances = []

        #: Shared function registry.
        #: funcname: (module, fn)
        self.shared_functions = {}

        self._sentry = sentry

        # load config and create module list
        self.parse_config()
        self._load_modules()

    def parse_config(self):
        self._parse_config(CONFIGS)

    def parse_args(self, args):
        self._parse_args(args,
                         usage='%(prog)s [opts] module1 [opts] [args] module2 ...',
                         epilog=self.module_group_list_usage(),
                         formatter_class=LineWrapRawTextHelpFormatter)

        # re-create logger - now we have all necessary configuration
        level = logging.INFO
        if self.option('debug') or self.option('verbose'):
            level = logging.VERBOSE

            if not self.option('verbose'):
                level = logging.DEBUG

        elif self.option('quiet'):
            level = logging.WARNING

        logger = Logging.create_logger(output_file=self.option('output'), level=level,
                                       colors=(self.option('colors') is not None),
                                       sentry=self._sentry)

        self.logger = ContextAdapter(logger)
        self.logger.connect(self)

    def destroy_modules(self, failure=None):
        if not self.module_instances:
            return

        # we will destroy modules in reverse order, which makes more sense
        self.verbose('destroying all modules in reverse order')

        for module in reversed(self.module_instances):
            try:
                module.debug('destroying myself')
                module.destroy(failure=failure)

            # pylint: disable=broad-except
            except Exception as exception:
                exc_info = sys.exc_info()

                self.exception('error in destroy function: {}'.format(str(exception)))
                self.sentry_submit_exception(exc_info)

        self.module_instances = []

    def add_module_instance(self, module):
        self.module_instances.append(module)
        return module

    def init_module(self, module):
        return self.modules[module]['class'](self)

    def run_module(self, module_name, args):
        module = self.init_module(module_name)

        # Process options from all sources
        module.parse_config()
        module.parse_args(args)
        module.sanity()
        module.check_required_options()

        module.execute()
        module.add_shared()

    def module_list(self):
        return sorted(self.modules)

    def module_list_usage(self, groups):
        """ Returns a string with modules description """

        if groups:
            usage = [
                'Available modules in {} group(s)'.format(', '.join(groups))
            ]
        else:
            usage = [
                'Available modules'
            ]

        # get module list
        plist = self.module_group_list()
        if not plist:
            usage.append('')
            usage.append('  -- no modules found --')
        else:
            for group in sorted(plist):
                # skip groups that are not in the list
                # note that groups is [] if all groups should be shown
                if groups and group not in groups:
                    continue
                usage.append('')
                usage.append('%-2s%s' % (' ', group))
                for key, val in sorted(plist[group].iteritems()):
                    usage.append('%-4s%-32s %s' % ('', key, val))

        return '\n'.join(usage)

    def module_group_list(self):
        """ Returns a dictionary of groups of modules with description """
        module_groups = {}
        for module in self.module_list():
            group = self.modules[module]['group']
            try:
                module_groups[group].update({
                    module: self.modules[module]['description']
                })
            except KeyError:
                module_groups[group] = {
                    module: self.modules[module]['description']
                }
        return module_groups

    def module_group_list_usage(self):
        """ Returns a string with all available groups """
        ret = 'Available module groups: '

        # get module list
        glist = self.module_group_list()
        return ret + ', '.join(glist)

    def print_cmdline(self, ci_args, modules_args):
        """
        Logs command-line that would recreate current process.
        """

        from .utils import format_command_line

        cmdline = [
            [sys.argv[0]] + ci_args
        ]

        for module in modules_args:
            module_name = module.keys()[0]

            cmdline.append([module_name] + module[module_name])

        self.info('command-line info:\n{}'.format(format_command_line(cmdline)))
