import os

from libci import Module


class CIMTF(Module):
    name = 'mtf'

    options = {
        'fedmsgfile': {
            'help': 'file containing received fedmsg'
        },
        'module': {
            'help': 'which module to test',
            'default': 'testing-module'
        }
    }

    required_options = ['fedmsgfile']

    def execute(self):
        fedmsgf = os.path.abspath(self.option('fedmsgfile'))
        module = self.option('module')

        self.shared('jenkins').set_build_name(module)

        guests = self.shared('provision', image='Fedora-Cloud-Base-25-compose-latest')
        assert guests and len(guests) == 1, 'bad provision'

        guest = guests[0]

        setupcmds = [
            'dnf copr -y enable jscotka/modularity-testing-framework',
            'dnf install -y modularity-testing-framework'
        ]

        map(guest.execute, setupcmds)
        guest.copy_to(fedmsgf, '/tmp/message.yaml')

        try:
            guest.execute("bash /usr/share/moduleframework/tools/run-them.sh {} /tmp/message.yaml".format(module))

        finally:
            guest.copy_from('/root/avocado', '.', recursive=True)
