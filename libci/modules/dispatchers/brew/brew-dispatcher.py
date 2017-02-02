import os
import re
import shlex
import yaml

from libci import Module
from libci import CIError


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
            raise CIError('file \'{}\' does not exist'.format(config))

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
        try:
            self.default_tests = self.config['default']
        except KeyError:
            self.default_tests = []

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
            if self.config['packages'][self.build['name']]:
                return self.config['packages'][self.build['name']]
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
                    raise CIError("Required option '{}' not found in the environment or command line".format(option))
                self.build[option] = self.option(option)

    def dispatch_tests(self):
        for test in self.get_tests():
            self.verbose("dispatching module '{}' for enabled package '{}' for target '{}'".format(
                test, self.build['name'], self.build['target']))

            module = shlex.split(test)[0]
            args = shlex.split(test)[1:]

            print 'module:' + module
            print args
            self.run_module(module, args)

    def execute(self):
        self.dispatch_tests()
        # self.info("package '{}' not enabled for target '{}'".format(self.build['name'], self.build['target']))
