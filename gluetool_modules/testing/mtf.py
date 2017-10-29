import tempfile

import gluetool
from gluetool import GlueError, GlueCommandError


class CIMTF(gluetool.Module):
    name = 'mtf'
    description = 'Provides basic access to restraint client.'

    options = {
        'test-module': {
            'help': 'which module to test',
            'default': 'testing-module'
        }
    }

    def execute(self):
        self.require_shared('trigger_message', 'image', 'provision')

        trigger_message = self.shared('trigger_message')

        module = self.option('test-module')

        if module is None:
            module = trigger_message.get('msg', {}).get('name', None)

            if module is None:
                # pylint: disable=line-too-long
                raise GlueError("Cannot find module to test - either use --test-module option, or provide 'trigger_message' shared function")  # Ignore PEP8Bear

        self.shared('jenkins').set_build_name(module)

        image = self.shared('image')
        if image is None:
            raise GlueError('No image provided')

        guests = self.shared('provision', image=image)

        if not guests or len(guests) != 1:
            raise GlueError('No guest provided')

        guest = guests[0]

        # Fedora does not have Python2, while Ansible needs Python2...
        guest.execute('dnf install -y python2 python2-dnf libselinux-python')

        # Store message into a file which we then copy to the guest
        message_file = tempfile.NamedTemporaryFile()
        message_file.close()

        gluetool.utils.dump_yaml(trigger_message, message_file.name, logger=self.logger)

        guest.setup(variables={
            'FEDMSG_FILE': message_file.name
        })

        try:
            cmd = 'bash /usr/share/moduleframework/tools/run-them.sh {} /tmp/message.yaml'.format(module)
            output = guest.execute(cmd)

        except GlueCommandError as exc:
            output = exc.output

            self.error('Test command exited with exit code {} - see debug log for details'.format(output.exit_code))

        finally:
            guest.copy_from('/root/avocado', '.', recursive=True)

        self.info('Result of testing: {}'.format('PASS' if output.exit_code == 0 else 'FAIL'))
