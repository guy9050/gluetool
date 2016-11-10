from libci import Plugin
from libci import libciError

class ComponentSwitch(Plugin):
    name = 'component-switch'
    desc = 'Run component switch task on given jenkins instance'
    cswitch = 'ci-workflow-component-switch'

    def execute(self):
        jenkins = self.shared('jenkins')
        if not jenkins:
            raise libciError('no shared jenkins connection found')

        print jenkins.version
