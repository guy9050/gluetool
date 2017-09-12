import os

from libci import Module, CIError, CICommandError


class CIMTF(Module):
    name = 'mtf'

    options = {
        'fedmsgfile': {
            'help': 'file containing received fedmsg'
        },
        'test-module': {
            'help': 'which module to test',
            'default': 'testing-module'
        }
    }

    required_options = ['fedmsgfile']

    def execute(self):
        fedmsgf = os.path.abspath(self.option('fedmsgfile'))
        module = self.option('test-module')

        self.shared('jenkins').set_build_name(module)

        if not self.has_shared('image'):
            raise CIError('No image provided, did you run guess-*-image module?')

        image = self.shared('image')
        if image is None:
            raise CIError('No image provided')

        if not self.has_shared('provision'):
            raise CIError('No guest provider found, did you run a guests provider module, e.g. openstack?')

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
