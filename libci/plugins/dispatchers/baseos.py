import os
import re
import yaml

from libci import Plugin
from libci import libciError

class CIBaseOSDispatcher(Plugin):
    name = 'baseos'
    desc = 'Configurable dispatcher for BaseOS'
    python_requires = 'PyYAML'
    config = None

    options = {
        'config': {
            'help': 'BaseOS dispatcher configuration'
        },
        'id': {
            'help': 'Brew task id',
        },
        'nvr': {
            'help': 'Package NVR',
        },
        'list': {
            'help': 'List dispatcher configuration',
        },
        'dispatch-all': {
            'help': 'Dispatch all builds (default false)',
            'action': 'store_true',
        },
        'scratch': {
            'help': 'Scratch build (default false)',
            'default': False,
        },
        'target': {
            'help': 'Package brew target',
        },
        'verify': {
            'help': 'Verify dispatcher configuration',
        },
    }
    required_options = ['config', 'id', 'nvr', 'target']

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
        self.packages = [p for p in self.config['packages'].keys()]
        self.debug('packages: {}'.format(self.packages))

        # defaults
        self.default_tests = self.config['defaults']['tests']
        self.default_targets = self.config['defaults']['targets']

    def verify(self):
        pass

    def check_target(self, package, target):
        try:
            if self.config['packages'][package]['targets']:
                targets = self.config['packages'][package]['targets']
        except (KeyError, TypeError):
            targets = self.default_targets

        for t in targets:
            if re.search(t, target):
                return True
        return False

    def get_tests(self, package):
        try:
            if self.config['packages'][package]['tests']:
                return self.config['packages'][package]['tests']
        except (KeyError, TypeError):
            return self.default_tests

    def check_options(self):
        nvr = self.option('nvr')
        if not re.match('.*-.*-.*', nvr):
            msg = '\'{}\' is not a valid NVR'.format(nvr)
            raise libciError(msg)

    def dispatch_tests(self, package):
        target = self.option('target')
        nvr = self.option('nvr')
        scratch = self.option('scratch')
        taskid = self.option('id')
        jenkins = self.shared('jenkins')
        for test in self.get_tests(package):
            msg = 'dispatching job \'{}\''.format(test)
            msg += ' for enabled package \'{}\''.format(package)
            msg += ' for target \'{}\''.format(target)
            self.info(msg)
            job = jenkins[test]
            job.invoke(test, build_params={'id': taskid,
                                           'nvr': nvr,
                                           'target': target,
                                           'scratch': scratch})

    def execute(self):
        if not self.shared('jenkins'):
            raise libciError('no jenkins connection found')

        target = self.option('target')
        nvr = self.option('nvr')
        dispatch_all = self.option('dispatch-all')
        package = re.search('(.*)-.*-.*', nvr).group(1)

        # parse configuration
        self.parse_yaml()

        if package not in self.packages and not dispatch_all:
            self.info('package \'{}\' not enabled'.format(package))
            return

        if dispatch_all or self.check_target(package, target):
            self.dispatch_tests(package)
        else:
            msg = 'package \'{}\' not enabled for '.format(package)
            msg += 'target \'{}\''.format(target)
            self.info(msg)
