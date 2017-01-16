import argparse
import ast
import atexit
import ConfigParser
import imp
import os
import sys
import datetime
import traceback

from functools import partial


CONFIGS = ['/etc/citool', os.path.expanduser('~/.citool.d/citool')]
MODULE_CONFIG_PATHS = ['/etc/citool.d/config',
                       os.path.expanduser('~/.citool.d/config')]
MODULE_PATH = [os.path.dirname(os.path.abspath(__file__)) + '/modules']
DATA_PATH = os.path.dirname(os.path.abspath(__file__)) + '/data'


class libciError(Exception):
    """ General libci exception """
    pass


class libciRetryError(libciError):
    """ Retry libci exception """
    pass


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
                if isinstance(e, libciError):
                    raise libciRetryError(e.value)
                else:
                    raise libciRetryError(e)
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
    # Calling the Module.add_options function the options defined
    # here will be added to the passed parser group.
    #
    # By calling Module.init_options you can add options to the
    # module manually
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

    # a list of shared function names
    shared_functions = None

    def __init__(self, ci):
        #
        # instance specific variables
        #

        # initialize citool
        self.ci = ci

        # initialize logging helpers
        self.debug = partial(self.log, level='D')
        self.verbose = partial(self.log, level='V')
        self.info = partial(self.log)
        self.warn = partial(self.log, level='W')

        # configuration parser
        self.config_parser = None

        # config values
        # Here are stored configuration values passed via (in this order)
        # a) configuration file
        # b) command line arguments
        # c) init_options function
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
        # add/update shared functions
        if self.shared_functions:
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
                raise libciError('Missing required \'{}\' option'.format(opt))

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

            for opt in list(self.options.keys()):
                try:
                    value = self.config_parser.get('default', opt)
                    dmsg = 'Added option \'%s\' value \'%s\'' % (opt, value)
                    dmsg += ' from config'
                    self._config[opt] = value
                    self.debug(dmsg)
                except ConfigParser.NoOptionError:
                    pass
                except ConfigParser.NoSectionError:
                    pass

    def shared_functions_help(self):
        if not self.shared_functions:
            return ''
        docs = "\nshared functions:\n"
        for func in self.shared_functions:
            if getattr(self, func).__doc__:
                docs += '  {}\t{}\n'.format(func, getattr(self, func).__doc__)
            else:
                docs += '  {}\tno documentation added :(\n'.format(func)
        return docs

    def parse_args(self, args):
        """
        parse options from command line
        """
        # add module's parsed options
        parser = argparse.ArgumentParser(
            usage='Usage: %s [options]' % self.name,
            description=self.__doc__,
            epilog=self.shared_functions_help(),
            formatter_class=argparse.RawTextHelpFormatter)
        if self.options:
            for opt in sorted(list(self.options.keys())):
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
            for opt in list(self.options.keys()):
                try:
                    value = getattr(options, opt.replace('-', '_'))
                    if value is None and opt in self._config:
                        continue
                    dmsg = 'Added option \'%s\' value \'%s\'' % (opt, value)
                    dmsg += ' from commandline'
                    self._config[opt] = value
                    self.debug(dmsg)
                except AttributeError:
                    pass

    def init_options(self, **kwargs):
        """
        add options to the module manually
        """
        # TODO: check self._config['value'] - shouldn't there be key instead?
        for key, value in kwargs.iteritems():
            try:
                self._config['value'] = value
            except KeyError:
                raise KeyError('option %s not recognized by this module')

    def run_module(self, module, args=[]):
        # FIXME: args=None, args = args or [], unless it's really intended
        self.ci.run_module(module, args)

    def log(self, msg, level=None):
        """
        Implements the actual output of logging messages. Prefixes each message
        with module name, and passes it to parent's `log` method.

        :param string level: If set, denotes debug level other than "info". 'D' as "debug",
            'V' as "verbose", and "W" as "warning" are supported.
        :param string msg: the actual message.

        """

        self.ci.log('[%s] %s' % (self.name, msg), level=level)


class Ci(object):
    # configuration
    config_parser = None

    # module types dictionary
    modules = {}
    module_instances = []

    # shared functions
    shared_functions = {}

    # add a shared function, overwrite if exists
    def add_shared(self, funcname, module):
        self.shared_functions.update({funcname: module})

    # delete a shared function if exists
    def del_shared(self, funcname):
        try:
            self.shared_functions.pop(funcname, None)
        except KeyError:
            pass

    # call a shared function
    def shared(self, funcname, *args, **kwargs):
        if funcname not in self.shared_functions:
            return None
        function = getattr(self.shared_functions[funcname], funcname)
        return function(*args, **kwargs)

    def _load_config(self):
        self.config_parser = ConfigParser.ConfigParser()
        self.config_parser.read(CONFIGS)
        if self.config_parser.has_section('default'):
            for item in list(self.config.keys()):
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
                except Exception as e:
                    self.info('ignoring module \'{0}\' from \'{1}\' group (error: {2})'.format(
                        mname, group, str(e)))
                    continue

                for name in dir(module):
                    cls = getattr(module, name)
                    try:
                        if issubclass(cls, Module) and cls != Module:
                            if not cls.name:
                                error = 'no module name specified'
                                raise libciError(error)
                            if cls.name in self.modules:
                                # pprint.pprint(self.modules)
                                msg = '\'%s\' is a duplicate' % cls.name
                                msg += ' module name \'%s/' % group
                                msg += '%s\'' % filepath
                                raise libciError(msg)
                            # add to modules dictionary
                            self.modules[cls.name] = {
                                'class': cls,
                                'description': cls.description,
                                'group': group,
                            }
                    except TypeError:
                        pass

    # find all available modules
    def __init__(self):
        # configuration defaults
        self.config = {
            'data_path': None,
            'debug': False,
            'output': None,
            'info': True,
            'list': None,
            'module_path': None,
            'quiet': False,
            'retries': 0,
            'verbose': False,
            'version': False,
        }

        # Initialize logging methods before douing anything else
        self.debug = partial(self.log, level='D')
        self.verbose = partial(self.log, level='V')
        self.info = self.log
        self.warn = partial(self.log, level='W')

        self.output_file = None

        # load config and create module list
        self._load_config()
        self._load_modules()

    def call_module_destroy(self, module):
        try:
            module.debug('destroying myself')
            module.destroy()
        except Exception as e:
            exstr = 'error in destroy function: %s\n' % str(e)
            sys.stderr.write(exstr)
            if self.get_config('verbose') or self.get_config('debug'):
                traceback.print_exc()

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
        for opt in list(self.config.keys()):
            value = getattr(parsed_args, opt.replace('-', '_'))
            self.config[opt] = value

        # open output file if needed
        if self.config['output']:
            self.output_file = open(self.config['output'], 'w')
            atexit.register(self._close_output_file)

    def module_list(self):
        return sorted(list(self.modules.keys()))

    def module_list_usage(self, groups):
        """ Returns a string with modules description """
        ret = 'Available modules'
        if groups:
            ret += ' in %s group(s)' % ', '.join(groups)
        ret += ':\n'
        # get module list
        plist = self.module_group_list()
        if not plist:
            ret += '\n  -- no modules found --'
        else:
            for group in sorted(list(plist.keys())):
                # skip groups that are not in the list
                # note that groups is [] if all groups should be shown
                if groups and group not in groups:
                    continue
                ret += '\n%-2s%s\n' % (' ', group)
                for key, val in sorted(plist[group].iteritems()):
                    ret += '%-4s%-32s %s\n' % ('', key, val)
        return ret

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

    def log(self, msg, level=None):
        """
        Implements the actual output of logging messages. Based on its level,
        writes the message to proper stream, and adds a copy to output file
        if it's opened.

        :param string level: If set, denotes debug level other than "info". 'D' as "debug",
            'V' as "verbose", and "W" as "warning" are supported.
        :param string msg: the actual message.
        """

        msg = '[{0}] [{1}] {2}\n'.format(
            '+' if level is None else level,
            datetime.datetime.now().strftime('%X.%f'),
            msg)

        if level == 'D' and self.config['debug']:
            sys.stderr.write(msg)

        elif level == 'V' and (self.config['verbose'] or self.config['debug']):
            sys.stderr.write(msg)

        elif level == 'W':
            sys.stderr.write(msg)

        elif level is None and not self.config['quiet']:
            sys.stdout.write(msg)

        if self.output_file:
            self.output_file.write(msg)

    def _close_output_file(self):
        """
        If opened, close output file used for logging.

        This method was registered with atexit.
        """

        if self.output_file is None:
            return

        self.debug('closing output file \'{}\''.format(self.config['output']))

        # To make sure we have all buffered output written - close() does *not* guarantee
        # flushing of internal buffer(s).
        self.output_file.flush()
        self.output_file.close()

        self.output_file = None
