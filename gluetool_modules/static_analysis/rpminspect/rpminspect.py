import os
import re
import shutil
import tempfile

import gluetool
from gluetool import GlueError, GlueCommandError
from gluetool.utils import Command, check_for_commands, load_json, normalize_multistring_option
from gluetool.log import log_dict
from libci.results import TestResult, publish_result

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import cast, Any, Callable, Dict, List, Optional, Tuple, Type, Union  # noqa

# map RPMinspect test score to resultsdb 2.0 API outcome states
# http://docs.resultsdb20.apiary.io/
# note: waived is mapped to info
RPMINSPECT_MAP = ['PASSED', 'INFO', 'INFO', 'NEEDS_INSPECTION', 'FAILED']

# Helping dict for calculating overall_result
RPMINSPECT_SCORE = {
    'OK': 0,
    'INFO': 1,
    'WAIVED': 2,
    'VERIFY': 3,
    'BAD': 4
}

TEST_NAMES = [
    'License',
    'Payload',
    'Header Metadata',
    'Man pages',
    'XML files',
    'ELF object properties',
    'Desktop Entry files'
]

# required commands of module
REQUIRED_CMDS = ['rpminspect']


class RpminspectTestResult(TestResult):
    """
    RPMinspect test result data container

    :param str test_type: one of 'analysis', 'comparison'
    :param str overall_result: general result of a test
    """
    # pylint: disable=too-few-public-methods

    def __init__(self, glue, test_type, overall_result, **kwargs):
        # type: (gluetool.glue.Glue, str, str, **Any) -> None

        super(RpminspectTestResult, self).__init__(glue, 'rpminspect-{}'.format(test_type), overall_result, **kwargs)
        self.rpminspect_test_type = test_type

    @property
    def test_results(self):
        # type: () -> Dict[str, str]
        """
        Return dict with name of test as a key
        and it result as a value

        :rtype: dict
        :returns Dictionary with results of every test
        """
        test_results = {}

        # first value is an overall result, skip it
        for result in self.payload[1:]:
            test_name = result['testcase']['name'].split('.')[-1]
            test_results[test_name] = result['outcome']

        return test_results


class RpminspectSkippedTestResult(TestResult):
    """
    RPMinspect test result data container for a skipped test result
    """
    # pylint: disable=too-few-public-methods

    def __init__(self, glue, **kwargs):
        # type: (gluetool.glue.Glue, **Any) -> None
        super(RpminspectSkippedTestResult, self).__init__(glue, 'rpminspect-comparison', 'INFO', **kwargs)
        self.rpminspect_test_type = 'comparison'
        self.test_results = {'ALL': 'SKIPPED'}  # notification for users, will be visible in email


class CIRpminspect(gluetool.Module):

    name = 'rpminspect'
    description = 'Run RPMinspect analysis or comparison'

    # pylint: disable=gluetool-option-hard-default
    options = {
        'type': {
            'help': 'Test type: analysis or comparison (default: %(default)s)',
            'metavar': 'TYPE',
            'type': str,
            'choices': ['analysis', 'comparison'],
            'default': 'comparison'
        },
        'tests': {
            'help': 'List of tests to perform. If nothing is set, all tests would run (default: ALL)',
            'metavar': 'TESTS',
            'action': 'append',
            'choices': ['ALL', 'license', 'emptyrpm', 'metadata', 'manpage', 'xml', 'elf'],
            'default': []
        },
        'results-file': {
            'help': 'A file for storing not formated rpminspect results (default: %(default)s)',
            'metavar': 'FILE',
            'type': str,
            'default': ''
        },
        'verbose-log-file': {
            'help': 'A file for storing verbose log of rpminspect run (default: %(default)s)',
            'metavar': 'FILE',
            'type': str,
            'default': ''
        }
    }

    def sanity(self):
        # type: () -> None

        check_for_commands(REQUIRED_CMDS)

    def _rpminspect_cmd(self, tests, workdir):
        # type: (List[str], str) -> List[str]

        cmd = [
            'rpminspect',
            '-v',
            # give a subfolder as a workdir to rpminspect for easy deleting it later
            '-w', os.path.join(workdir, 'artifacts'),
            '-o', os.path.join(workdir, self.option('results-file')),
            '-F', 'json',
            '-T', ','.join(tests) if tests else 'ALL'
        ]
        return cmd

    def _run_rpminspect(self, task, tests, workdir):
        # type: (Any, List[str], str) -> None
        """
        Execute RPMinspect analysis or comparison based on test type.
        Store results and verbose log to a separate files.

        :param task: a task for analysis
        :param workdir: a workdir for storing logs and temporary artifacts
        """

        command = self._rpminspect_cmd(tests, workdir)

        if self.option('type') == 'comparison':
            if task.latest is None:
                raise GlueError('Not provided baseline for comparison')
            command.append(task.latest)

        command.append(task.nvr)

        try:
            output = Command(command).run()
        except GlueCommandError as exc:
            raise GlueError('rpminspect failed during execution with: {}'.format(exc))

        # output is verbose log, store it to a file
        if output.stdout is not None:
            with open(os.path.join(workdir, self.option('verbose-log-file')), 'w') as output_file:
                output_file.write(output.stdout)

    def _publish_skipped_result(self, task):
        # type: (Any) -> None
        """
        Publish a skipped test result.
        """
        result = [{
            'data': {
                'item': task.nvr,
                'type': 'brew_build_pair',
                'scratch': task.scratch,
                'taskid': task.id
            },
            'ref_url': '',
            'testcase': {
                'name': 'dist.rpminspect.comparison',
                'ref_url': '',
            },
            'outcome': 'INFO',
            'note': 'No baseline found for the build. Testing skipped'
        }]

        publish_result(self, RpminspectSkippedTestResult, payload=result)

    def _parse_runinfo(self, task, json_output):
        # type: (Any, Dict[str, List[Dict[str, str]]]) -> Any
        """
        Return parsed runinfo into known structure.

        :param task: info about task
        :param dict runinfo: informations about RPMinspect run
        """
        test_type = self.option('type')

        if test_type == 'comparison':
            result_type = 'brew_build_pair'
            item = '{} {}'.format(task.nvr, task.latest)
        else:
            result_type = 'brew_build'
            item = task.nvr

        # Get the worst result of all tests is overall
        overall_result = 'OK'
        for test_info in json_output.values():
            for test_entry in test_info:
                if RPMINSPECT_SCORE[test_entry['result']] > RPMINSPECT_SCORE[overall_result]:
                    overall_result = test_entry['result']

        # Map to result consistent with resultsdb
        overall_result = RPMINSPECT_MAP[RPMINSPECT_SCORE[overall_result]]

        # Basic result data and overall result
        payload = [{
            'data': {
                'item': item,
                'type': result_type,
                'newnvr': task.nvr,
                'oldnvr': task.latest,
                'scratch': task.scratch,
                'taskid': task.id
            },
            'ref_url': '',
            'testcase': {
                'name': 'dist.rpminspect.{}'.format(test_type),
                'ref_url': ''
            },
            'outcome': overall_result
        }]

        def _parse_results(data):
            # type: (Dict[str, List[Dict[str, str]]]) -> List[Dict[str, Any]]
            parsed_results = []

            # Parse results for every test.
            # Passed if test doesn't have any output
            for test_name in TEST_NAMES:

                test_info = data[test_name] if test_name in data.keys() else {}

                # Return the worst result from test
                # pylint: disable=cell-var-from-loop
                def _outcome():
                    # type: () -> str
                    if not test_info:
                        return 'PASSED'

                    return RPMINSPECT_MAP[
                        max([RPMINSPECT_SCORE[test_entry['result']] for test_entry in test_info])
                    ]

                # pylint: disable=cell-var-from-loop
                def _test_outputs():
                    # type: () -> List[Dict[str, str]]
                    test_outputs = []
                    for test_entry in test_info:
                        output = {}
                        if 'message' in test_entry:
                            output['message'] = test_entry['message']
                        if 'result' in test_entry:
                            output['result'] = RPMINSPECT_MAP[RPMINSPECT_SCORE[test_entry['result']]]
                        if 'screendump' in test_entry:
                            output['screendump'] = test_entry['screendump']
                        if 'remedy' in test_entry:
                            output['remedy'] = test_entry['remedy']
                        if 'waiver authorization' in test_entry:
                            output['waiver_authorization'] = test_entry['waiver authorization']

                        if output:
                            test_outputs.append(output)
                    return test_outputs

                # Make lowercase test_name, change spaces to underlines
                description = re.sub('[ ]', '_', test_name.lower())
                parsed_results.append({
                    'data': {
                        'item': item,
                        'type': result_type,
                        'newnvr': task.nvr,
                        'oldnvr': task.latest,
                        'scratch': task.scratch,
                        'taskid': task.id
                    },
                    'ref_url': '',
                    'testcase': {
                        'name': 'dist.rpminspect.{}.{}'.format(test_type, description),
                        'ref_url': '',
                        'test_outputs': _test_outputs() if test_info else []
                    },
                    'outcome': _outcome()
                })
            return parsed_results

        payload.extend(_parse_results(json_output))
        return payload

    def _publish_results(self, task, json_output):
        # type: (Any, Dict[str, List[Dict[str, str]]]) -> None

        payload = self._parse_runinfo(task, json_output)
        overall_result = payload[0]['outcome']
        publish_result(self, RpminspectTestResult, self.option('type'), overall_result, payload=payload)

    def execute(self):
        # type: () -> None

        # Module create workdir with logs and artifacts which is very large.
        # Finally block deletes artifact after execution even the error occurs.
        # It can't be done in destroy method for multithread supporting.
        try:
            tests = normalize_multistring_option(self.option('tests'))
            workdir = tempfile.mkdtemp(dir=os.getcwd())
            test_type = self.option('type')

            self.require_shared('primary_task')
            task = self.shared('primary_task')

            if test_type == 'comparison':
                if not task.latest:
                    self.warn('no baseline found, refusing to continue testing')
                    self._publish_skipped_result(task)
                    return
                if task.latest == task.nvr:
                    self.warn('cowardly refusing to compare same packages')
                    return

            msg = ["running {} for task '{}'".format(test_type, task.id)]
            msg += ['compared to {}'.format(task.latest)] if test_type == 'comparison' else []
            self.info(' '.join(msg))

            self._run_rpminspect(task, tests, workdir)

            json_results = load_json(os.path.join(workdir, self.option('results-file')))

            log_dict(self.info, 'rpminspect returned', json_results)
            self._publish_results(task, json_results)

        finally:
            if os.path.exists(os.path.join(workdir, 'artifacts')):
                shutil.rmtree(os.path.join(workdir, 'artifact'))