import re
import libci.results
import gluetool
from gluetool.utils import Command, normalize_multistring_option


class BuildTestResult(libci.results.TestResult):

    def __init__(self, glue, overall_result, **kwargs):
        super(BuildTestResult, self).__init__(glue, 'functional', overall_result, **kwargs)


class BrewBuilder(gluetool.Module):

    name = 'brew-builder'
    description = 'Triggers scratch brew build'

    options = {
        'arches': {
            'help': 'List of arches to build (default: none).',
            'action': 'append',
            'default': []
        },
    }

    def execute(self):
        self.require_shared('src_rpm')

        src_rpm_name = self.shared('src_rpm')

        self.info('Initializing brew scratch build')

        command = [
            'rhpkg', 'scratch-build',
            '--srpm', src_rpm_name,
            '--nowait'
        ]

        arches = normalize_multistring_option(self.option('arches'))

        if arches:
            command += ['--arches', ' '.join(arches)]

        output = Command(command).run()

        # detect brew task id
        match = re.search(r'^Created task: (\d+)$', output.stdout, re.M)
        if not match:
            raise gluetool.GlueError('Unable to find `task-id` in `rhpkg` output')
        task_id = match.group(1)

        # detect brew task URL and log it
        match = re.search(r'^Task info: (.+)$', output.stdout, re.M)
        if not match:
            raise gluetool.GlueError('Unable to find `task-url` in `rhpkg` output')
        task_url = match.group(1)
        self.info('Waiting for brew to finish task: {0}'.format(task_url))

        # wait until brew task finish
        command = ['brew', 'watch-task', task_id]
        output = Command(command).run()

        if output.exit_code == 0:
            result = 'PASS'
        else:
            result = 'FAIL'

        libci.results.publish_result(self, BuildTestResult, result)
