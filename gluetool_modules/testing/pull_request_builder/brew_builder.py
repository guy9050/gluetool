import re
import libci.results
import gluetool
from gluetool.utils import Command, normalize_multistring_option
import gluetool_modules.libs
from gluetool_modules.libs.brew_build_fail import run_command


class BrewBuildTestResult(libci.results.TestResult):

    # pylint: disable=too-many-arguments
    def __init__(self, glue, overall_result, build_url, comment, process_output, **kwargs):
        super(BrewBuildTestResult, self).__init__(glue, 'brew-build', overall_result, **kwargs)

        self.build_url = build_url
        self.comment = comment
        self.process_output = process_output


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

    def report_result(self, result, build_url=None, exception=None):
        self.info('Result of testing: {}'.format(result))

        comment = exception.message if exception else None
        process_output = exception.output if exception else None

        libci.results.publish_result(self, BrewBuildTestResult, result, build_url, comment, process_output)

    def _make_brew_build(self):
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
        brew_watch_cmd = ['brew', 'watch-task', task_id]

        run_command(
            self,
            Command(brew_watch_cmd),
            'Wait for brew build finish'
        )

        return task_url

    def execute(self):
        try:
            brew_task_url = self._make_brew_build()
        except gluetool_modules.libs.brew_build_fail.BrewBuildFailedError as exc:
            self.report_result('FAIL', exception=exc)
            return

        self.report_result('PASS', build_url=brew_task_url)
