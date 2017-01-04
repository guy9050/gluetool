import os
import re
import subprocess
import yaml

from libci import Module
from libci import libciError


class CIBrewDispatcher(Module):
    """A configurable dispatcher for Brew builds """
    name = 'brew-dispatcher'
    description = 'Configurable brew dispatcher'
    python_requires = 'PyYAML'
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
        #'list': {
        #    'help': 'List dispatcher configuration',
        #},
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
        #'verify': {
        #    'help': 'Verify dispatcher configuration',
        #},
    }
    required_options = ['config']

    def parse_yaml(self):
        config = os.path.expanduser(self.option('config'))

        # check if configuration exists
        if not os.path.exists(config):
            raise libciError('file \'{}\' does not exist'.format(config))

        # read yaml configuration
        stream = file(config, 'r')
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
            if self.config['packages'][self.name]['targets']:
                targets = self.config['packages'][self.name]['targets']
        except (KeyError, TypeError):
            targets = self.default_targets

        for t in targets:
            if re.search(t, self.target):
                return True
        return False

    def get_tests(self):
        try:
            if self.config['packages'][self.name]['tests']:
                return self.config['packages'][self.name]['tests']
        except (KeyError, TypeError):
            return self.default_tests

    def sanity(self):
        # parse configuration
        self.parse_yaml()

        # set options from command line
        for option in ['name', 'version', 'release', 'target', 'id']:
            try:
                setattr(self, option, os.environ[option])
            except KeyError:
                # for cmdline options replace '_' with '-'
                cmdopt = self.option(option.replace('_', '-'))
                if not cmdopt:
                    msg = 'Required option \'{}\' not found'.format(option)
                    msg += ' in the environment or command line'
                    raise libciError(msg)
                setattr(self, option, cmdopt)

    def dispatch_tests(self):
        for test in self.get_tests():
            msg = 'dispatching module \'{}\''.format(test)
            msg += ' for enabled package \'{}\''.format(self.name)
            msg += ' for target \'{}\''.format(self.target)
            module = test.split()[0]
            args = test.split()[1:]

            # replace $ with self variable
            for i, arg in enumerate(args):
                if arg[0] == '$':
                    try:
                        value = getattr(self, arg[1:])
                        msg = 'replacing \'{}\' with internal value'.format(arg)
                        msg += ' \'{}\' of \'{}\''.format(arg[1:], value)
                        self.verbose(msg)
                        args[i] = value
                    except:
                        raise libciError('could not replace \'{}\''.format(arg))

            self.run_module(module, args)

    def execute(self):
        dispatch_all = self.option('dispatch-all')

        if self.name not in self.names and not dispatch_all:
            self.info('package \'{}\' not enabled'.format(self.name))
            return

        if dispatch_all or self.check_target(self.name, self.target):
            self.dispatch_tests()
        else:
            msg = 'package \'{}\' not enabled for '.format(self.name)
            msg += 'target \'{}\''.format(self.target)
            self.info(msg)
