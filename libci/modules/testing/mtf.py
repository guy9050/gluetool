import os

from libci import Module, CIError, CICommandError


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

        self.require_shared('image', 'provision')

        image = self.shared('image')
        if image is None:
            raise CIError('No image provided')

        guests = self.shared('provision', image=image)

        if not guests or len(guests) != 1:
            raise CIError('No guest provided')

        guest = guests[0]

        setupcmds = [
            'dnf -y copr enable phracek/meta-test-family-devel',
            'dnf -y install  meta-test-family'
        ]

        map(guest.execute, setupcmds)
        guest.copy_to(fedmsgf, '/tmp/message.yaml')

        try:
            cmd = 'bash /usr/share/moduleframework/tools/run-them.sh {} /tmp/message.yaml'.format(module)
            output = guest.execute(cmd)

        except CICommandError as exc:
            output = exc.output

            self.error('Test command exited with exit code {} - see debug log for details'.format(output.exit_code))

        finally:
            guest.copy_from('/root/avocado', '.', recursive=True)

        self.info('Result of testing: {}'.format('PASS' if output.exit_code == 0 else 'FAIL'))
