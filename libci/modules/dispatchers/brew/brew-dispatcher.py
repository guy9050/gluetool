import os
import re
import yaml

from libci import Module
from libci import CiError


class CIBrewDispatcher(Module):
    """A configurable dispatcher for Brew builds """
    name = 'brew-dispatcher'
    description = 'Configurable brew dispatcher'
    python_requires = 'PyYAML'
    config = None
    build = dict()

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
        'dispatch-all': {
            'help': 'Dispatch all builds (default: false)',
            'action': 'store_true',
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

    def parse_yaml(self):
        config = os.path.expanduser(self.option('config'))

        # check if configuration exists
        if not os.path.exists(config):
            raise CiError('file \'{}\' does not exist'.format(config))

        # read yaml configuration
        with open(config, 'r') as stream:
            self.config = yaml.load(stream)
        self.debug('config: {}'.format(self.config))

        # enabled packages
        try:
            self.names = [p for p in self.config['packages'].keys()]
        except KeyError:
            self.names = []
        self.debug('packages: {}'.format(self.names))

        # defaults
        self.default_tests = self.config['defaults']['tests']
        self.default_targets = self.config['defaults']['targets']

    def verify(self):
        pass

    def check_target(self):
        try:
            if self.config['packages'][self.build['name']]['targets']:
                targets = self.config['packages'][self.build['name']]['targets']
        except (KeyError, TypeError):
            targets = self.default_targets

        for target in targets:
            if re.search(target, self.build['target']):
                return True
        return False

    def get_tests(self):
        try:
            if self.config['packages'][self.build['name']]['tests']:
                return self.config['packages'][self.build['name']]['tests']
        except (KeyError, TypeError):
            return self.default_tests

    def sanity(self):
        # parse configuration
        self.parse_yaml()

        # set options from command line or environment
        for option in ['name', 'version', 'release', 'target', 'id']:
            try:
                self.build[option] = os.environ[option]
            except KeyError:
                # for cmdline options replace '_' with '-'
                if not self.option(option):
                    msg = 'Required option \'{}\' not found'.format(option)
                    msg += ' in the environment or command line'
                    raise CiError(msg)
                self.build[option] = self.option(option)

    def dispatch_tests(self):
        for test in self.get_tests():
            msg = 'dispatching module \'{}\''.format(test)
            msg += ' for enabled package \'{}\''.format(self.build['name'])
            msg += ' for target \'{}\''.format(self.build['target'])
            module = test.split()[0]
            args = test.split()[1:]

            # replace $var with build variable var
            for i, arg in enumerate(args):
                if arg[0] == '$':
                    try:
                        value = self.build[arg[1:]]
                        msg = 'replacing \'{}\' with internal value'.format(arg)
                        msg += ' \'{}\' of \'{}\''.format(arg[1:], value)
                        self.verbose(msg)
                        args[i] = value
                    except KeyError:
                        msg = 'could not replace \'{}\''.format(arg)
                        msg += ', not found among \'{}\''.format(','.join(self.build.keys()))
                        raise CiError('could not replace \'{}\''.format(arg))

            self.run_module(module, args)

    def execute(self):
        dispatch_all = self.option('dispatch-all')

        if self.build['name'] not in self.names and not dispatch_all:
            self.info('package \'{}\' not enabled'.format(self.build['name']))
            return

        if dispatch_all or self.check_target(self.build['name'],
                                             self.build['target']):
            self.dispatch_tests()
        else:
            msg = 'package \'{}\' not enabled for '.format(self.build['name'])
            msg += 'target \'{}\''.format(self.build['target'])
            self.info(msg)
