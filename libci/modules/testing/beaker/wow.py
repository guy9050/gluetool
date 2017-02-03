from libci import CIError, CICommandError, Module, utils

REQUIRED_COMMANDS = ['bkr', 'beaker-jobwatch']


class CIWow(Module):
    """
    This module just wraps beaker workflow tomorrow and injects --distro
    from shared 'distro' function if available.
    """
    name = 'wow'
    args = []

    def sanity(self):
        utils.check_for_commands(REQUIRED_COMMANDS)

    def parse_args(self, args):
        self.args = args or []
        if not self.args:
            raise CIError('parameters are required for bkr workflow-tomorrow')

    def execute(self):
        distro = ['--distro', self.shared('distro')] if self.shared('distro') else []
        task = self.shared('brew_task')
        brew_task = ['--brew-task', str(task.task_id)] if task is not None else []
        whiteboard = "CI run for '{}' for brew task id '{}'".format(task.nvr, task.task_id)
        try:
            command = ['bkr', 'workflow-tomorrow', '--id', '--whiteboard', whiteboard]
            command += distro + brew_task + self.args
            output = utils.run_command(command)
        except CICommandError as exc:
            raise CIError(exc.output.stderr)
        self.info('wow run succesfully with output:\n{}'.format(output.stderr))

        # beaker-jobwatch
        jobs = []
        for job in output.stdout.split():
            jobs += ['--job', job]
        try:
            command = ['beaker-jobwatch'] + jobs
            self.info('running "{}"'.format(' '.join(command)))
            utils.run_command(command, stdout=utils.PARENT)
        except CICommandError as exc:
            raise CIError(exc.output.stderr)
