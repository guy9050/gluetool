import shlex

import gluetool


class ExecuteCommand(gluetool.Module):
    """
    Run an arbitrary command, or their sequence, and log the output.
    """

    name = 'execute-command'
    description = 'Run an arbitrary command, or their sequence, and log the output.'

    options = {
        'command': {
            'help': 'Command to run.',
            'type': str,
            'action': 'append',
            'default': []
        },
        'script': {
            'help': 'Script - YAML file with a list of commands - to execute.',
            'type': str,
            'action': 'append',
            'default': []
        }
    }

    def sanity(self):
        if not self.option('command') and not self.option('script'):
            raise gluetool.GlueError("You have to use either `--command` or `--script`")

        if self.option('command') and self.option('script'):
            raise gluetool.GlueError("You have to use just one of `--command` or `--script`")

    def execute(self):
        commands = []

        if self.option('command'):
            commands = self.option('command')

        else:
            for script in gluetool.utils.normalize_path_option(self.option('script')):
                script_commands = gluetool.utils.load_yaml(script, logger=self.logger)

                if not isinstance(script_commands, list):
                    raise gluetool.GlueError("Script '{}' does not contain a list of commands".format(script))

                commands += script_commands

        context = self.shared('eval_context')

        for command in commands:
            original_command = command

            self.info('Running command: {}'.format(command))

            command = gluetool.utils.render_template(command, logger=self.logger, **context)

            split_command = shlex.split(command)

            try:
                output = gluetool.utils.run_command(split_command)

            except gluetool.GlueCommandError as exc:
                output = exc.output

            (self.info if output.exit_code == 0 else self.error)('Exited with code {}'.format(output.exit_code))
            gluetool.log.log_blob(self.info, 'stdout', output.stdout)
            gluetool.log.log_blob(self.error, 'stderr', output.stderr)

            if output.exit_code != 0:
                raise gluetool.GlueError("Command '{}' exited with non-zero exit code".format(original_command))
