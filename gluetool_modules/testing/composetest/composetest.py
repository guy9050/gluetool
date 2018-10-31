import os

import gluetool
import libci.results

# Mapping of exit codes to tuple of task result string and explanation
COMPOSECI_EXIT_CODES = {
    0: {'status': 'PASSED', 'msg': 'composeci finished successfully'},
    10: {'status': 'FAILED', 'msg': 'running "brew dist-repo" failed'},
    11: {'status': 'FAILED', 'msg': 'running "lorax" failed'},
    12: {'status': 'FAILED', 'msg': 'initializing mock chroot with lorax failed'},
    13: {'status': 'FAILED', 'msg': 'wrong tag configuration provided'},  # maybe this should be ERROR?
}


class ComposeTestResult(libci.results.TestResult):
    """ ComposeTest test result data container """

    # pylint: disable-msg=too-many-arguments
    def __init__(self, glue, overall_result, tag_configuration, difflog, output_end, **kwargs):
        super(ComposeTestResult, self).__init__(
            glue, 'composetest', overall_result, **kwargs
        )
        self.difflog = difflog
        self.output_end = output_end
        self.tag_configuration = tag_configuration


class ComposeTest(gluetool.Module):
    """
    ComposeTest module

    This module runs composeci, which is a framework for testing various
    parts of the compose process. The results of the run are passed
    via the results shared function.
    """

    name = 'composetest'
    description = 'Run compose process testing.'

    options = {
        'tag-configuration': {
            'help': 'Tag configuration of ComposeCI to use (default: %(default)s)',
            'default': 'rhel-8.0.0',
        },
        # for now we just pass x86_64 as the only arch
        # 'arches': {
        # },
        'db-url': {
            'help': 'SQLAlchemy compatbile DB URL (default: %(default)s).',
            'default': 'sqlite:////tmp/composeci.db',
        },
        'no-trigger-event': {
            'help': 'Use when this run is not triggered by a specific event (default: %(default)s).',
            'action': 'store_true',
            'default': False,
        },
        'diff-log-file': {
            'help': 'File to log informative diffs for use in notification emails (default: %(default)s).',
            'default': 'composetest-difflog',
        },
    }

    def _get_difflog(self):
        dlf = self.option('diff-log-file')
        if os.path.exists(dlf):
            with open(dlf) as f:
                return f.read()
        return 'Diffs logfile was not generated'

    def _publish_results(self, cmd_res, tag_configuration, difflog, output_end):
        overall_result = COMPOSECI_EXIT_CODES.get(cmd_res.exit_code, {}).get('status', 'FAILED')
        libci.results.publish_result(
            self,
            ComposeTestResult,
            overall_result=overall_result,
            tag_configuration=tag_configuration,
            difflog=difflog,
            output_end=output_end,
        )

    def execute(self):
        package = None
        tag_configuration = self.option('tag-configuration')

        if self.option('no-trigger-event') is False:
            self.require_shared('trigger_message')
            msg = self.shared('trigger_message')
            # We listen to:
            # * /topic/VirtualTopic.eng.brew.build.> (package builds)
            # * /topic/VirtualTopic.eng.brew.package.> (tag actions, e.g. blocking pkg from tag)
            # so trigger will either be build or package
            trigger = msg['topic'].split('.')[-2].lower()

            if trigger == 'build':
                self.require_shared('primary_task')
                package = self.shared('primary_task').nvr
            elif trigger == 'package':
                package = msg['headers']['package']
                tag_configuration = msg['headers']['tag']
            else:
                raise gluetool.GlueError(
                    'Unknown trigger {} from topic {}'.format(trigger, msg['topic'])
                )

        cmd = ['composeci', '--db-url', self.option('db-url'), 'test-buildinstall']
        if package is not None:
            cmd.extend(['--package', package])
        for opt in self.options:
            value = self.option(opt)
            if opt in ['db-url', 'no-trigger-event']:
                continue  # skip db-url here
            cmd.extend(['--{opt}'.format(opt=opt), value])

        try:
            res = gluetool.utils.Command(cmd).run(inspect=True)
        except gluetool.GlueCommandError as exc:
            res = exc.output
        stderr_end = '\n'.join(res.stderr.splitlines()[-30:])

        result = 'success'
        if res.exit_code != 0:
            result = 'failure - exit code {code}: {desc}'.format(
                code=res.exit_code,
                desc=COMPOSECI_EXIT_CODES.get(res.exit_code, {}).get('msg', 'unknown exit code')
            )
        self.info('result: {}'.format(result))
        self._publish_results(
            res,
            tag_configuration=tag_configuration,
            difflog=self._get_difflog(),
            output_end=stderr_end
        )
