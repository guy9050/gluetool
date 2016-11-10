from libci import Plugin
from libci import libciError

class Rpmdiff(Plugin):
    name = 'rpmdiff'
    desc = 'Schedule RPMdiff run and wait for results'

    opts = {
        'taskid': {
            'help': 'Brew task id',
        }
    }

    def execute(self):
        print self.get_opt('taskid')
