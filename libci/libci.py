import imp
import os
import sys
import traceback

import ConfigParser
from ConfigParser import NoOptionError, NoSectionError

import argparse

CONFIGS = ['/etc/citool', os.path.expanduser('~/.citool')]
PLUGIN_CONFIG_PATHS = ['/etc/citool.d/config',
                       os.path.expanduser('~/.citool.d/config')]
PLUGIN_PATH = [os.path.dirname(os.path.abspath(__file__)) + '/plugins']


class libciError(Exception):
    """ should be used for raising exceptions"""
    def __init__(self, value):
        self.value = value

    def __str__(self):
        """ decode from utf-8 in case of pshell output in exception """
        if isinstance(self.value, (str, basestring, unicode)):
            return self.value.encode('utf-8')
        else:
            return self.value


class libciRetryError(libciError):
    """ should be used for raising exceptions that cause a retry"""
    def __init__(self, value):
        self.value = value

    def __str__(self):
        """ decode from utf-8 in case of pshell output in exception """
        if isinstance(self.value, (str, basestring, unicode)):
            return self.value.encode('utf-8')
        else:
            return self.value


# retry decorator, use as
# @retry(exception1, exception2, ..)
def retry(*args):
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


class Plugin(object):
    #
    # static variables, same in all instances
    #
    name = None   # default: group-module_name (without .py suffix)
    desc = None   # description, displayed in plugin list
    epilog = None  # detailed help for -h, same as description if not specified

    # The options variable defines additional plugin options
    # the required_options defines a list of required plugin options
    #
    # Calling the Plugin.add_options function the options defined
    # here will be added to the passed parser group.
    #
    # By calling Plugin.init_options you can add options to the
    # plugin manually
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

        # configuration parser
        self.config_parser = None

        # config values
        # Here are stored configuration values passed via (in this order)
        # a) configuration file
        # b) command line arguments
        # c) init_options function
        self._config = {}

    def destroy(self):
        """
        here should go any code that needs to be run on exit.
        like job cleanup etc.
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
        execute is a required plugin function
        """
        raise NotImplementedError

    def check_options(self):
        """
        additional plugin options checking
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
        get an option value from the plugin config
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
            self.debug('Parsing {}'.format([os.path.join(c, self.name) for c in PLUGIN_CONFIG_PATHS]))
            self.config_parser.read([os.path.join(c, self.name) for c in PLUGIN_CONFIG_PATHS])
            for opt in list(self.options.keys()):
                try:
                    value = self.config_parser.get('default', opt)
                    dmsg = 'Added option \'%s\' value \'%s\'' % (opt, value)
                    dmsg += ' from config'
                    self._config[opt] = value
                    self.debug(dmsg)
                except NoOptionError:
                    pass
                except NoSectionError:
                    pass

    def parse_args(self, args):
        """
        parse options from command line
        """
        # add plugin's parsed options
        parser = argparse.ArgumentParser(
            usage='Usage: %s [options]' % self.name,
            description=self.desc,
            epilog=self.epilog)
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

    def init_options(self, *args, **kwargs):
        """
        add options to the plugin manually
        """
        for key, value in kwargs.iteritems():
            try:
                self._config['value'] = value
            except KeyError:
                raise KeyError('option %s not recognized by this plugin')

    def debug(self, msg):
        self.ci.debug('[%s] %s' % (self.name, msg))

    def verbose(self, msg):
        self.ci.verbose('[%s] %s' % (self.name, msg))

    def info(self, msg):
        if not self.ci.config['quiet']:
            sys.stdout.write('[+ %s] %s\n' % (self.name, msg))


class Ci(object):
    # configuration
    config_parser = None

    # plugin types dictionary
    plugins = {}
    plugin_instances = []

    # shared functions
    shared_functions = {}

    # add a shared function, overwrite if exists
    def add_shared(self, funcname, plugin):
        self.shared_functions.update({funcname: plugin})

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
        if self.config_parser.has_section('citool'):
            for item in list(self.config.keys()):
                if self.config_parser.has_option('default', item):
                    self.config[item] = self.config_parser.get('default', item)

    def get_config(self, key):
        return self.config[key]

    def _load_plugins(self):
        ppaths = PLUGIN_PATH + self.get_config('plugin_path')
        self.debug('loading plugins from these paths: {}'.format(ppaths))
        for ppath in ppaths:
            self._load_plugin_path(ppath)

    def _load_plugin_path(self, ppath):

        # load all .py files in plugins path
        # load them as libci.name
        # all Plugins subclasses are added to the self.plugins dictionary
        for root, dirs, files in os.walk(ppath):
            for file in sorted(files):
                if not file.endswith('.py'):
                    continue
                group = root.replace(ppath + '/', '')
                mname, _ = os.path.splitext(file)
                try:
                    module = imp.load_source('libci.%s-%s' %
                                             (group, mname),
                                             os.path.join(root, file))
                except Exception as e:
                    self.info('ignoring module \'%s\' ' % mname +
                                'from \'%s\' group' % group +
                                ' (error: %s)' % str(e))
                    continue
                for name in dir(module):
                    cls = getattr(module, name)
                    try:
                        if issubclass(cls, Plugin) and cls != Plugin:
                            if not cls.name:
                                error = 'no plugin name specified'
                                raise libciError(error)
                            if cls.name in self.plugins:
                                # pprint.pprint(self.plugins)
                                msg = '\'%s\' is a duplicate' % cls.name
                                msg += ' plugin name \'%s/' % group
                                msg += '%s\'' % file
                                raise libciError(msg)
                            # add to plugins dictionary
                            self.plugins[cls.name] = {
                                'class': cls,
                                'desc': cls.desc,
                                'group': group,
                            }
                    except TypeError:
                        pass

    # find all available plugins
    def __init__(self):
        # configuration defaults
        self.config = {
            'debug': False,
            'info': True,
            'quiet': False,
            'retries': 0,
            'verbose': False,
            'version': False,
            'list': None,
            'plugin_path': [],
        }

        # load config and create plugin list
        self._load_config()
        self._load_plugins()

    def call_plugin_destroy(self, plugin):
        try:
            plugin.debug('destroying myself')
            plugin.destroy()
        except Exception as e:
            exstr = 'error in destroy function: %s\n' % str(e)
            sys.stderr.write(exstr)
            if self.get_config('verbose') or self.get_config('debug'):
                traceback.print_exc()

    def destroy_plugins(self):
        if self.plugin_instances:
            self.verbose('destroying all plugins in reverse order')
            # we will destroy plugins in reverse order, which makes more sense
            for plugin in reversed(self.plugin_instances):
                self.call_plugin_destroy(plugin)
        self.plugin_instances = []

    def add_plugin_instance(self, plugin_name):
        plugin = self.plugins[plugin_name]['class'](self)
        self.plugin_instances.append(plugin)
        return plugin

    def parse_args(self, args):
        usage = '%(prog)s [opts] plugin1 [opts] [args] plugin2 ...'

        parser = argparse.ArgumentParser(usage=usage,
                                         epilog=self.plugin_group_list_usage())
        parser.add_argument('-d', '--debug', action='store_true',
                            default=False, help='Debug output')
        parser.add_argument('-i', '--info', action='store_true',
                            default=False,
                            help='Print information about commandline')
        parser.add_argument('-l', '--list', nargs='*', metavar='GROUP',
                            help='List all available plugins or given GROUPs')
        parser.add_argument('-p', '--plugin-path', action='append',
                            default=[], help='Specify plugin path')
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

    def plugin_list(self):
        return sorted(list(self.plugins.keys()))

    def plugin_list_usage(self, groups):
        """ Returns a string with plugins description """
        ret = 'Available plugins'
        if groups:
            ret += ' in %s group(s)' % ', '.join(groups)
        ret += ':'
        # get plugin list
        plist = self.plugin_group_list()
        if not plist:
            ret += '\n  -- no plugins found --'
        else:
            for group in sorted(list(plist.keys())):
                # skip groups that are not in the list
                # note that groups is [] if all groups should be shown
                if groups and group not in groups:
                    continue
                ret += '\n%-2s%s\n' % (' ', group)
                for key, val in sorted(plist[group].iteritems()):
                    ret += '%-4s%-16s %s\n' % ('', key, val)
        return ret

    def plugin_group_list(self):
        """ Returns a dictionary of groups of plugins with description """
        plugin_groups = {}
        for plugin in self.plugin_list():
            group = self.plugins[plugin]['group']
            try:
                plugin_groups[group].update({
                    plugin: self.plugins[plugin]['desc']
                })
            except KeyError:
                plugin_groups[group] = {
                    plugin: self.plugins[plugin]['desc']
                }
        return plugin_groups

    def plugin_group_list_usage(self):
        """ Returns a string with all available groups """
        ret = 'Available plugin groups: '

        # get plugin list
        glist = self.plugin_group_list()
        return ret + ', '.join(glist)

    def print_cmdline(self, ci_args, plugins_args):
        """ prints info about current run """
        self.info('command-line info')
        sys.stdout.write('{0} {1}'.format(sys.argv[0], ' '.join(ci_args)))
        for plugin in plugins_args:
            sys.stdout.write(' \\\n  {0} {1}'.format(plugin.keys()[0],
                             ' '.join(plugin.values()[0])))
        sys.stdout.write('\n')

    def debug(self, string):
        if self.config['debug']:
            sys.stderr.write('[D] {}\n'.format(string))

    def verbose(self, string):
        if self.config['verbose']:
            sys.stderr.write('[V] {}\n'.format(string))

    def info(self, string):
        if not self.config['quiet']:
            sys.stdout.write('[+] {}\n'.format(string))
