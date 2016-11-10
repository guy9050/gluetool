from libci import Plugin


class CITestOnBuild(Plugin):
    name = 'testonbuild'
    desc = 'Schedule beakerlib tests'

    options = {
        'id': {
            'help': 'Brew task id',
        },
        'nvr': {
            'help': 'Package NVR'
        },
        'scratch': {
            'help': 'Scratch build (default: False)',
            'default': False,
        },
        'target': {
            'help': 'Brew build target'
        },
    }
    required_options = ['id', 'nvr', 'target']

    def execute(self):
        nvr = self.option('nvr')
        target = self.option('target')
        scratch = self.option('scratch')
        taskid = self.option('id')
        msg = 'Running {} for '.format(self.name)
        if scratch == 'true' or scratch == 'True':
            msg += 'scratch'
        msg += 'build of \'{}\' '.format(nvr)
        msg += 'with task id \'{}\' on target \'{}\''.format(taskid, target)
        self.info(msg)
