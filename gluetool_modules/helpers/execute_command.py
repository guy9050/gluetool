import shlex

import gluetool


class ExecuteCommand(gluetool.Module):
    """
    Run an arbitrary command, or their sequence, and log the output.
    """

    name = 'execute-command'
    description = 'Run an arbitrary command, or their sequence, and log the output.'

    # pylint: disable=gluetool-option-no-default-in-help
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
        },
        'on-destroy': {
            'help': 'Execute commands when destroying the module, not when executing it (default: %(default)s).',
            'action': 'store_true',
            'default': 'no'
        }
    }

    def sanity(self):
        if not self.option('command') and not self.option('script'):
            raise gluetool.GlueError("You have to use either `--command` or `--script`")

        if self.option('command') and self.option('script'):
            raise gluetool.GlueError("You have to use just one of `--command` or `--script`")

    def _execute_commands(self, context_extra=None):
        commands = []

        if self.option('command'):
            commands = self.option('command')

        else:
            for script in gluetool.utils.normalize_path_option(self.option('script')):
                script_commands = gluetool.utils.load_yaml(script, logger=self.logger)

                if not isinstance(script_commands, list):
                    raise gluetool.GlueError("Script '{}' does not contain a list of commands".format(script))

                commands += script_commands

        context = gluetool.utils.dict_update(
            self.shared('eval_context'),
            context_extra or {}
        )

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

    def execute(self):
        if gluetool.utils.normalize_bool_option(self.option('on-destroy')):
            return

        self._execute_commands()

    def destroy(self, failure=None):
        if not gluetool.utils.normalize_bool_option(self.option('on-destroy')):
            return

        self._execute_commands(context_extra={
            'FAILURE': failure
        })