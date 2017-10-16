import shlex

import libci


class ExecuteCommand(libci.Module):
    """
    Run an arbitrary command, or their sequence, and log the output.
    """

    name = 'execute-command'

    options = {
        'command': {
            'help': 'Command to run.',
            'type': str,
            'action': 'append',
            'default': []
        }
    }

    required_options = ('command',)

    def execute(self):
        for command in self.option('command'):
            self.info('Running: {}'.format(command))

            split_command = shlex.split(command)

            try:
                output = libci.utils.run_command(split_command)

            except libci.CICommandError as exc:
                output = exc.output

            (self.info if output.exit_code == 0 else self.error)('Exited with code {}'.format(output.exit_code))
            libci.log.log_blob(self.info, 'stdout', output.stdout)
            libci.log.log_blob(self.error, 'stderr', output.stderr)

            if output.exit_code != 0:
                raise libci.CIError("Command '{}' exited with non-zero exit code".format(command))
