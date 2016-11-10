from libci import Plugin
from libci import libciError


class CIComponentSwitch(Plugin):
    name = 'cswitch'
    desc = 'Run component switch task on given jenkins instance'
    cswitch = 'ci-workflow-component-switch'

    options = {
        'covscan': {
            'help': 'Enable/disable covscan (default: leave)',
            'default': 'leave',
        },
        'rpmdiff': {
            'help': 'Enable/disable rpmdiff (default: leave)',
            'default': 'leave',
        },
        'tob': {
            'help': 'Enable/disable test-on-build (default: leave)',
            'default': 'leave',
        },
    }

    def execute(self):
        jenkins = self.shared('jenkins')
        if not jenkins:
            raise libciError('no shared jenkins connection found')

        rpmdiff = self.option('rpmdiff')
        covscan = self.option('covscan')
        tob = self.option('tob')
        jenkins.invoke(self.cswitch, build_params={'RPMDIFF': rpmdiff,
                                                   'COVSCAN': covscan,
                                                   'TEST_ON_BUILD': tob})
