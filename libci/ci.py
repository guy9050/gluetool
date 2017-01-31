import argparse
import ast
import ConfigParser
import imp
import logging
import os
import sys

from .log import Logging


CONFIGS = ['/etc/citool', os.path.expanduser('~/.citool.d/citool')]
MODULE_CONFIG_PATHS = ['/etc/citool.d/config',
                       os.path.expanduser('~/.citool.d/config')]
MODULE_PATH = [os.path.dirname(os.path.abspath(__file__)) + '/modules']
DATA_PATH = os.path.dirname(os.path.abspath(__file__)) + '/data'


class CIError(Exception):
    """ General libci exception """
    pass


class CIRetryError(CIError):
    """ Retry libci exception """
    pass


class CICommandError(CIError):
    """
    Exception raised when external command failes.
    """

    def __init__(self, cmd, output):
        super(CICommandError, self).__init__("Command '{}' failed with exit code {}".format(cmd, output.exit_code))

        self.cmd = cmd
        self.output = output


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


class Module(object):
    #
    # static variables, same in all instances
    #
    name = None         # default: group-module_name (without .py suffix)
    description = None  # short description, displayed in module list

    # The options variable defines additional module options
    # the required_options defines a list of required module options
    #
    # 'option_name' defines the long option name (i.e. --option_name')
    # {
    #    'option_name' : {
    #        'action' : 'store',        # The default action is 'store'
    #        'default' : default_value, # The default value for the option
    #        'help' : 'Option help',    # Option help
    #         ... any other argparse args
    #    },
    # }
    options = {}
    required_options = None

    #: A list of names of module's shared functions
    shared_functions = []

    def __init__(self, ci):
        #
        # instance specific variables
        #

        # initialize citool
        self.ci = ci

        # initialize logging helpers
        # definitely could be done in loop + setattr but pylint can't decode that :(
        logger = Logging.get_logger()
        self.verbose = logger.verbose
        self.debug = logger.debug
        self.info = logger.info
        self.warn = logger.warn
        self.error = logger.error
        self.exception = logger.exception

        # configuration parser
        self.config_parser = None

        # config values
        # Here are stored configuration values passed via (in this order)
        # a) configuration file
        # b) command line arguments
        self._config = {}

        # initialize data path if exists, else it will be None
        dpath = os.path.join(self.ci.get_config('data_path') or DATA_PATH, self.name)
        self.data_path = dpath if os.path.exists(dpath) else None

    def destroy(self):
        # pylint: disable-msg=no-self-use

        """
        Here should go any code that needs to be run on exit, like job cleanup etc.
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
        - Advanced checks on passed options
        - Check for additional requirements (tools, data, etc.)
        """

        return None

    def check_required_options(self):
        if not self.required_options:
            self.debug('skipping checking of required options')
            return
        for opt in self.required_options:
            if opt not in self._config or not self._config[opt]:
                raise CIError('Missing required \'{}\' option'.format(opt))

    def shared(self, *args, **kwargs):
        return self.ci.shared(*args, **kwargs)

    def option(self, opt):
        """
        get an option value from the module config
        """
        try:
            return self._config[opt]
        except KeyError:
            return None

    def init_options_config(self):
        """
        parse options default values from the configuration file
        """
        if self.options:
            self.config_parser = ConfigParser.ConfigParser()
            paths = [os.path.join(c, self.name) for c in MODULE_CONFIG_PATHS]
            self.debug('Parsing {}'.format(paths))
            self.config_parser.read(paths)

            for opt in self.options:
                try:
                    value = self.config_parser.get('default', opt)
                    self._config[opt] = value
                    self.debug("Added option '{}' value '{}' from config".format(opt, value))
                except ConfigParser.NoOptionError:
                    pass
                except ConfigParser.NoSectionError:
                    pass

    @staticmethod
    def _trim_docstring(docstring):
        """
        Quoting `PEP 257 <https://www.python.org/dev/peps/pep-0257/#handling-docstring-indentation>`:

        *Docstring processing tools will strip a uniform amount of indentation from
        the second and further lines of the docstring, equal to the minimum indentation
        of all non-blank lines after the first line. Any indentation in the first line
        of the docstring (i.e., up to the first newline) is insignificant and removed.
        Relative indentation of later lines in the docstring is retained. Blank lines
        should be removed from the beginning and end of the docstring.*

        Code bellow follows the quote.

        This method does exactly that, therefore we can keep properly aligned docstrings
        while still use them for reasonably formatted help texts.

        :param str docstring: raw docstring.
        :rtype: str
        :returns: docstring with lines stripped of leading whitespace.
        """

        if not docstring:
            return ''
        # Convert tabs to spaces (following the normal Python rules)
        # and split into a list of lines:
        lines = docstring.expandtabs().splitlines()
        # Determine minimum indentation (first line doesn't count):
        indent = sys.maxint
        for line in lines[1:]:
            stripped = line.lstrip()
            if stripped:
                indent = min(indent, len(line) - len(stripped))
        # Remove indentation (first line is special):
        trimmed = [lines[0].strip()]
        if indent < sys.maxint:
            for line in lines[1:]:
                trimmed.append(line[indent:].rstrip())
        # Strip off trailing and leading blank lines:
        while trimmed and not trimmed[-1]:
            trimmed.pop()
        while trimmed and not trimmed[0]:
            trimmed.pop(0)
        # Return a single string:
        return '\n'.join(trimmed)

    def shared_functions_help(self):
        if not self.shared_functions:
            return ''

        functions = []

        for funcname in self.shared_functions:
            func = getattr(self, funcname)

            if func.__doc__:
                functions.append('  {}\t{}\n'.format(funcname, self._trim_docstring(func.__doc__)))
            else:
                functions.append('  {}\tno documentation added :(\n'.format(funcname))

        return '\nshared functions:\n{}\n'.format('\n'.join(functions))

    def parse_args(self, args):
        """
        parse options from command line
        """
        # add module's parsed options
        parser = argparse.ArgumentParser(
            usage='Usage: %s [options]' % self.name,
            description=self._trim_docstring(self.__doc__),
            epilog=self.shared_functions_help(),
            formatter_class=argparse.RawTextHelpFormatter)
        if self.options:
            for opt in sorted(self.options):
                if 'short' in self.options[opt]:
                    short = self.options[opt].pop('short')
                    parser.add_argument('-%s' % short, '--%s' % opt,
                                        **self.options[opt])
                    self.options[opt]['short'] = short
                else:
                    parser.add_argument('--%s' % opt, **self.options[opt])

        # parse the added args
        options = parser.parse_args(args)

        # add the parsed args to options
        if self.options:
            for opt in self.options:
                try:
                    value = getattr(options, opt.replace('-', '_'))
                    if value is None and opt in self._config:
                        continue
                    self._config[opt] = value
                    self.debug("Added option '{}' value '{}' from commandline".format(opt, value))
                except AttributeError:
                    pass

    def run_module(self, module, args=None):
        self.ci.run_module(module, args or [])


class CI(object):
    # configuration
    config_parser = None

    # module types dictionary
    modules = {}
    module_instances = []

    #: Shared function registry.
    #: funcname: (module, fn)
    shared_functions = {}

    # add a shared function, overwrite if exists
    def add_shared(self, funcname, module):
        """
        Register a shared function. Overwrite previously registered function
        with the same name, if there was any such.

        :param str funcname: Name of the shared function.
        :param libci.Module module: Module instance providing the shared function.
        """

        if not hasattr(module, funcname):
            raise CIError("No such shared function '{}' of module '{}'".format(funcname, module.name))

        self.shared_functions[funcname] = (module, getattr(module, funcname))

    # delete a shared function if exists
    def del_shared(self, funcname):
        if funcname not in self.shared_functions:
            return

        del self.shared_functions[funcname]

    # call a shared function
    def shared(self, funcname, *args, **kwargs):
        if funcname not in self.shared_functions:
            return None

        return self.shared_functions[funcname][1](*args, **kwargs)

    def _load_config(self):
        self.config_parser = ConfigParser.ConfigParser()
        self.config_parser.read(CONFIGS)
        if self.config_parser.has_section('default'):
            for item in self.config:
                if self.config_parser.has_option('default', item):
                    self.config[item] = self.config_parser.get('default', item)

    def get_config(self, key):
        return self.config[key]

    def _load_modules(self):
        ppaths = self.get_config('module_path') or MODULE_PATH
        self.debug('loading modules from these paths: {}'.format(ppaths))
        for ppath in ppaths:
            self._load_module_path(ppath)

    @staticmethod
    def _check_module_file(mfile):
        """ Return True if if module file contains libci import and Module
            class definition """
        libci_found = False

        libci_module_found = False

        # print 'processing file: {}'.format(mfile)
        with open(mfile) as f:
            node = ast.parse(f.read())
            # print 'body: {}'.format(node.__dict__['body'])

            # check for libci import
            for item in node.__dict__['body']:
                # print 'processing item: {}'.format(item)
                if item.__class__.__name__ == 'Import':
                    if item.names[0].name == 'libci':
                        libci_found = True
                        break

                if item.__class__.__name__ == 'ImportFrom':
                    if item.module == 'libci':
                        libci_found = True
                        break

            # check for libci.Module class definition
            for item in node.__dict__['body']:
                # print 'processing item: {}'.format(item)
                if item.__class__.__name__ == 'ClassDef':
                    for base in item.bases:
                        try:
                            if base.id == 'Module':
                                libci_module_found = True
                                break
                        except AttributeError:
                            pass
                        try:
                            if base.attr == 'Module':
                                libci_module_found = True
                                break
                        except AttributeError:
                            pass

        # print 'found: {} {}'.format(libci_found, libci_module_found)
        if libci_found and libci_module_found:
            return True

        return False

    def _load_module(self, module, group, filepath):
        for name in dir(module):
            cls = getattr(module, name)
            try:
                if issubclass(cls, Module) and cls != Module:
                    if not cls.name:
                        error = 'no module name specified'
                        raise CIError(error)
                    if cls.name in self.modules:
                        # pprint.pprint(self.modules)
                        raise CIError("{}' is a duplicate module name '{}/{}'".format(cls.name, group, filepath))
                    # add to modules dictionary
                    self.modules[cls.name] = {
                        'class': cls,
                        'description': cls.description,
                        'group': group,
                    }
            except TypeError:
                pass

    def _load_module_path(self, ppath):
        """ Load modules from modules directory """
        for root, _, files in os.walk(ppath):
            for filepath in sorted(files):
                if not filepath.endswith('.py'):
                    continue
                group = root.replace(ppath + '/', '')
                mname, _ = os.path.splitext(filepath)
                mfile = os.path.join(root, filepath)
                # check if the file contains a valid libci module
                # note that various errors can happen here, just ignore
                # them (like permission denied, syntax error, ...)
                try:
                    if not self._check_module_file(mfile):
                        continue
                # pylint: disable=broad-except
                except Exception as e:
                    self.info('ignoring module \'%s\' ' % mname +
                              'from \'%s\' group' % group +
                              ' (error: %s)' % str(e))
                    continue
                # try to import the module
                try:
                    module = imp.load_source('libci.ci.%s-%s' %
                                             (group, mname),
                                             mfile)
                # pylint: disable=broad-except
                except Exception as e:
                    self.info('ignoring module \'{0}\' from \'{1}\' group (error: {2})'.format(
                        mname, group, str(e)))
                    continue

                self._load_module(module, group, filepath)

    def _init_logging(self, logger):
        # definitely could be done in loop + setattr but pylint can't decode that :(
        self.verbose = logger.verbose
        self.debug = logger.debug
        self.info = logger.info
        self.warn = logger.warn
        self.error = logger.error
        self.exception = logger.exception

    # find all available modules
    def __init__(self):
        # configuration defaults
        self.config = {
            'data_path': None,
            'debug': None,
            'output': None,
            'info': None,
            'list': None,
            'module_path': None,
            'quiet': None,
            'retries': None,
            'verbose': None,
            'version': None,
        }

        # Initialize logging methods before doing anything else.
        # Right now, we don't know the desired log level, or if
        # output file is in play, just get simple logger before
        # the actual configuration is known.
        self._init_logging(Logging.create_logger())

        # load config and create module list
        self._load_config()
        self._load_modules()

    def call_module_destroy(self, module):
        try:
            module.debug('destroying myself')
            module.destroy()
        # pylint: disable=broad-except
        except Exception:
            self.exception('error in destroy function')

    def destroy_modules(self):
        if self.module_instances:
            self.verbose('destroying all modules in reverse order')
            # we will destroy modules in reverse order, which makes more sense
            for module in reversed(self.module_instances):
                self.call_module_destroy(module)
        self.module_instances = []

    def add_module_instance(self, module):
        self.module_instances.append(module)
        return module

    def init_module(self, module):
        return self.modules[module]['class'](self)

    def run_module(self, module_name, args):
        module = self.init_module(module_name)
        module.init_options_config()
        module.parse_args(args)
        module.check_required_options()
        module.sanity()
        module.execute()
        module.add_shared()

    def parse_args(self, args):
        usage = '%(prog)s [opts] module1 [opts] [args] module2 ...'

        parser = argparse.ArgumentParser(usage=usage,
                                         epilog=self.module_group_list_usage(),
                                         formatter_class=argparse.RawTextHelpFormatter)
        parser.add_argument('--data-path', help='Specify data path')
        parser.add_argument('-d', '--debug', action='store_true',
                            default=False, help='Debug output')
        parser.add_argument('-i', '--info', action='store_true',
                            default=False,
                            help='Print information about commandline')
        parser.add_argument('-l', '--list', nargs='*', metavar='GROUP',
                            help='List all available modules or given GROUPs')
        # module path can be specified only from configuration file
        # suppress it from help
        parser.add_argument('--module-path', action='append',
                            default=[], help=argparse.SUPPRESS)
        parser.add_argument('-o', '--output',
                            help='Output debug/verbose/info to given file')
        parser.add_argument('-q', '--quiet', action='store_true',
                            default=False, help='Silence info messages')
        parser.add_argument('-r', '--retries', default=0,
                            help='Number of retries', type=int)
        parser.add_argument('-v', '--verbose', action='store_true',
                            default=False, help='Verbose output')
        parser.add_argument('-V', '--version', action='store_true',
                            default=False, help='Print version')

        # really parse the options
        parsed_args = parser.parse_args(args)

        # set the config dictionary from the parsed arguments
        for opt in self.config:
            value = getattr(parsed_args, opt.replace('-', '_'))
            if value is not None:
                self.config[opt] = value

        # re-create logger - now we have all necessary configuration
        level = logging.INFO
        if self.config['debug'] or self.config['verbose']:
            level = logging.VERBOSE

            if not self.config['verbose']:
                level = logging.DEBUG

        elif self.config['quiet']:
            level = logging.WARNING

        logger = Logging.create_logger(output_file=self.config['output'], level=level)
        self._init_logging(logger)

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
        """ prints info about current run """
        self.info('command-line info')
        sys.stdout.write('{0} {1}'.format(sys.argv[0], ' '.join(ci_args)))
        for module in modules_args:
            sys.stdout.write(' \\\n  {0} {1}'.format(
                module.keys()[0], ' '.join(module.values()[0])))
        sys.stdout.write('\n')
