import json
import re
import time

import gluetool
from gluetool import GlueError, SoftGlueError, GlueCommandError, utils
from gluetool.log import format_dict, log_blob
from libci.results import TestResult, publish_result

# map RPMdiff overal score to resultsdb 2.0 API outcome states
# http://docs.resultsdb20.apiary.io/
# note: waive is mapped to info
RPMDIFF_OVERALL_SCORE = {
    "Passed": "PASSED",
    "Info": "INFO",
    "Failed": "FAILED",
    "Needs inspection": "NEEDS_INSPECTION",
    "Waived": "INFO",
}

# map RPMdiff test result score to resultsdb 2.0 API outcome states
# http://docs.resultsdb20.apiary.io/
# note: waived is mapped to info
RPMDIFF_SCORE = {
    0: "PASSED",
    1: "PASSED",
    2: "INFO",
    3: "NEEDS_INSPECTION",
    4: "FAILED",
}

# required commands of module
REQUIRED_CMDS = ['rpmdiff-remote']


# NOT USED CURRENTLY, keeping here for future usage
class NoBaselineFoundError(SoftGlueError):
    STATUS = 'SKIP'

    def __init__(self):
        super(NoBaselineFoundError, self).__init__('Could not find baseline for this build')


class RpmdiffTestResult(TestResult):
    """
    RPMdiff test result data container

    :param dict runinfo: informations about RPMdiff run
    :param str test_type: one of 'analysis', 'comparison'
    """

    def __init__(self, glue, runinfo, test_type, **kwargs):
        overall_result = RPMDIFF_OVERALL_SCORE[runinfo['overall_score']['description']]

        ids = kwargs.pop('ids', {})
        ids.update({
            'rpmdiff_run_id': runinfo['run_id'],
        })

        urls = kwargs.pop('urls', {})
        urls.update({
            'rpmdiff_url': runinfo['web_url']
        })

        super(RpmdiffTestResult, self).__init__(glue, 'rpmdiff-{}'.format(test_type), overall_result,
                                                ids=ids, urls=urls, **kwargs)

    @property
    def rpmdiff_test_type(self):
        return self.test_type.split("-")[1]

    @classmethod
    def _unserialize_from_json(cls, glue, input_data):
        runinfo = {
            'overall_score': {
                'description': input_data['overall_result'].replace('_', ' ').lower().capitalize()
            },
            'run_id': input_data['ids']['rpmdiff_run_id'],
            'web_url': input_data['urls']['rpmdiff_url']
        }

        return RpmdiffTestResult(glue, runinfo, input_data['test_type'].split('-')[1],
                                 ids=input_data['ids'], urls=input_data['urls'], payload=input_data['payload'])

    def _serialize_to_xunit_property_dict(self, parent, properties, names):
        if 'rpmdiff_run_id' in properties:
            gluetool.utils.new_xml_element('property', parent, name='baseosci.id.rpmdiff-run',
                                           value=properties.pop('rpmdiff_run_id'))

        if 'rpmdiff_url' in properties:
            gluetool.utils.new_xml_element('property', parent, name='baseosci.url.rpmdiff-run',
                                           value=properties.pop('rpmdiff_url'))

        super(RpmdiffTestResult, self)._serialize_to_xunit_property_dict(parent, properties, names)


class RpmdiffSkippedTestResult(TestResult):
    """
    RPMdiff test result data container for a skipped test result
    """

    def __init__(self, glue, **kwargs):
        super(RpmdiffSkippedTestResult, self).__init__(glue, 'rpmdiff-comparison', 'INFO', **kwargs)

    @property
    def rpmdiff_test_type(self):
        return 'comparison'


class CIRpmdiff(gluetool.Module):
    """
    CI RPMdiff module

    This module schedules an RPMdiff run, waits until it is finished and reports
    results in results shared function.
    It is expected to run this module after Brew module.
    """

    name = 'rpmdiff'
    description = 'Run RPMdiff analysis or comparison'

    options = {
        'blacklist': {
            'help': 'A comma seaparted list of blacklisted package names',
        },
        'run-id': {
            'help': 'Do not schedule run, just report from given run id',
        },
        'type': {
            'help': 'Test type: analysis or comparison',
            'choices': ['analysis', 'comparison'],
        },
        'url': {
            'help': 'RPMdiff Hub URL',
        }
    }
    required_options = ['type']
    shared_functions = ['refresh_rpmdiff_results']

    _result_class = None

    def __init__(self, *args, **kwargs):
        super(CIRpmdiff, self).__init__(*args, **kwargs)
        self.task = None
        self.check_interval = 60
        self.max_timeout = 3600 * 4
        self.hub_url = None

    def sanity(self):
        utils.check_for_commands(REQUIRED_CMDS)

    @property
    def _rpmdiff_cmd(self):
        cmd = ["rpmdiff-remote"]
        if self.hub_url:
            cmd += ["--hub-url", self.hub_url]
        return cmd

    @staticmethod
    def _run_command(command):
        """
        Run external command.

        :param list(str) command: command line arguments as items of list
        :rtype: gluetool.utils.ProcessOutput instance
        :returns: :py:class:`gluetool.utils.ProcessOutput` instance whose attributes contain \
            data returned by the process.
        :raises gluetool.GlueError: when command was not found or command failed during execution
        """
        try:
            return utils.run_command(command)
        except GlueCommandError as exc:
            raise GlueError("Failure during 'rpmdiff-remote' execution: {}".format(exc.output.stderr))

    def _get_runinfo(self, run_id):
        """
        Execute rpmdiff-remote runinfo command to obtain runinfo.

        :param int run_id: ID of RPMdiff run
        :rtype: dict
        :returns: informations about RPMdiff run
        """
        # make sure run_id is a string here, as utils run_command requires it
        command = self._rpmdiff_cmd + ["runinfo", str(run_id)]

        blob = json.loads(CIRpmdiff._run_command(command).stdout)
        log_blob(self.debug, 'rpmdiff-remote runinfo returned', format_dict(blob))

        return blob

    def _wait_until_finished(self, run_id):
        """
        Helper function to ensure that run already finished.

        :param int run_id: ID of RPMdiff run
        :rtype: dict
        :returns: informations about RPMdiff run
        :raises gluetool.GlueError: when run timeout exceed, defined in self.max_timeout
        """
        start_time = time.time()
        runinfo = self._get_runinfo(run_id)
        self.verbose("RPMdiff run [{}] state: {}".format(run_id, runinfo['overall_score']['description']))
        while runinfo['overall_score']['description'] in ['Running', 'Queued for test']:
            if (time.time() - start_time) > self.max_timeout:
                raise GlueError("Waiting for RPMdiff results timed out ")
            time.sleep(self.check_interval)
            runinfo = self._get_runinfo(run_id)
        return runinfo

    def _run_rpmdiff(self, test_type, nvr_baseline=None):
        """
        Execute RPMdiff analysis or comparison based on test type

        :param str test_type: one of 'analysis', 'comparison'
        :param str or None nvr_baseline: package NVR against which RPMdiff comparison should be run,
            valid and required for test_type comparison
        :rtype: dict
        :returns: informations about RPMdiff run
        """
        if self.task.scratch:
            command = self._rpmdiff_cmd + ["schedule", str(self.task.id)]
        else:
            command = self._rpmdiff_cmd + ["schedule", self.task.nvr]

        if test_type == 'comparison':
            if nvr_baseline is None:
                raise GlueError("Not provided baseline for comparison")
            command += ["--baseline", nvr_baseline]

        blob = json.loads(CIRpmdiff._run_command(command).stdout)
        self.debug('rpmdiff-remote schedule returned:\n{}'.format(format_dict(blob)))
        self.info("web url: {}".format(blob['web_url']))
        return self._wait_until_finished(str(blob['run_id']))

    def _publish_skipped_result(self):
        """
        Publish a skipped test result.
        """
        result = [{
            'data': {
                'item': self.task.nvr,
                'type': 'koji_build',
                'scratch': self.task.scratch,
                'taskid': self.task.id
            },
            'ref_url': '',
            'testcase': {
                'name': 'dist.rpmdiff.comparison',
                'ref_url': 'https://url.corp.redhat.com/rpmdiff-in-ci',
            },
            'outcome': 'INFO',
            'note': 'No baseline found for the build. Testing skipped'
        }]

        publish_result(self, RpmdiffSkippedTestResult, payload=result)

    def _publish_results(self, runinfo, test_type):
        """
        Parse results into known structure and publish to results module.

        :param dict runinfo: informations about RPMdiff run
        :param str test_type: one of 'analysis', 'comparison'
        """
        if test_type == 'comparison':
            result_type = 'koji_build_pair'
            item = '{} {}'.format(self.task.nvr, self.task.latest)
        else:
            result_type = 'koji_build'
            item = self.task.nvr

        # basic result data and overall result
        tests = [{
            'data': {
                'item': item,
                'type': result_type,
                'newnvr': self.task.nvr,
                'oldnvr': self.task.latest,
                'scratch': self.task.scratch,
                'taskid': self.task.id
            },
            'ref_url': runinfo['web_url'],
            'testcase': {
                'name': 'dist.rpmdiff.{}'.format(test_type),
                'ref_url': 'https://url.corp.redhat.com/rpmdiff-in-ci',
            },
            'outcome': RPMDIFF_OVERALL_SCORE[runinfo['overall_score']['description']],
        }]

        def _parse_results(data):
            parsed_results = []
            for result in data['results']:
                description = re.sub('[^a-z0-9]', '_', result['test']['description'].lower())
                parsed_results.append({
                    'data': {
                        'item': item,
                        'type': result_type,
                        'newnvr': self.task.nvr,
                        'oldnvr': self.task.latest,
                        'scratch': self.task.scratch,
                        'taskid': self.task.id
                    },
                    'ref_url': '{}/{}'.format(runinfo['web_url'], result['test']['test_id']),
                    'testcase': {
                        'name': 'dist.rpmdiff.{}.{}'.format(test_type, description),
                        'ref_url': result['test']['wiki_url']
                    },
                    'outcome': RPMDIFF_SCORE[result['score']],
                })
            return parsed_results

        tests.extend(_parse_results(self._get_runinfo('{}/results'.format(runinfo['run_id']))))

        publish_result(self, RpmdiffTestResult, runinfo, test_type, payload=tests)

    def refresh_rpmdiff_results(self, run_id):
        """
        Shared function
        Download fresh results from RPMdiff service and updates results stored in results module.

        :param int run_id: ID of RPMdiff run
        :raises gluetool.GlueError: if shared function does not exist
        """
        self.require_shared('results')

        results = self.shared("results")
        old_results = []
        for result in results:
            if (result.test_type in ["rpmdiff-analysis", "rpmdiff-comparison"] and
                    result.ids['rpmdiff_run_id'] == run_id):
                old_results.append(result)
        if old_results:
            for result in old_results:
                results.remove(result)
            self._publish_results(self._get_runinfo(run_id), old_results[0].rpmdiff_test_type)

    def execute(self):
        blacklist = self.option('blacklist')
        run_id = self.option('run-id')
        test_type = self.option('type')
        url = self.option('url')

        if url:
            self.hub_url = url

        self.require_shared('primary_task')

        # get a brew task instance
        self.task = self.shared('primary_task')

        # blacklist packages
        if blacklist is not None:
            self.verbose('blacklisted packages: {}'.format(blacklist))
            if self.task.component in blacklist.split(','):
                self.info('skipping blacklisted package {}'.format(self.task.component))
                return

        if test_type == 'comparison':
            if self.task.latest is None:
                self.warn('no baseline found, refusing to continue testing')
                self._publish_skipped_result()
                return
            if self.task.scratch is False and self.task.latest == self.task.nvr:
                self.info('cowardly refusing to compare same packages')
                return

        msg = ["running {} for task '{}'".format(test_type, self.task.id)]
        msg += ['compared to {}'.format(self.task.latest)] if test_type == 'comparison' else []
        self.info(' '.join(msg))

        if run_id:
            runinfo = self._get_runinfo(run_id)
        else:
            runinfo = self._run_rpmdiff(test_type, self.task.latest)
        self.info('result: {}'.format(runinfo['overall_score']['description']))

        self._publish_results(runinfo, test_type)
