import json
import re
import time
from libci import Module
from libci import CIError, SoftCIError, CICommandError
from libci import utils
from libci.log import log_blob
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


# not used currently, keeping here for future usage
class NoBaselineFoundError(SoftCIError):
    STATUS = 'SKIP'
    SUBJECT = 'Could not find baseline for RPMDiff'
    BODY = """
CI skipped the testing due to the fact, that the baseline build for comparison was not found.
This can be caused by:

    * this is the first build of the package on this build target
    * there is some issue with build propagation for the build target

To check the tagged packages for given brew build you can user the 'brew' tool (e.g. for build
target 'rhel-7.4-candidate' and package bash):

    $ brew list-tagged rhel-7.4-candidate bash

Please file an issue to release enginnering if you encounter inconsistencies in  Brew by sending
out an email to 'release-engineering@redhat.com'.
    """

    def __init__(self):
        super(NoBaselineFoundError, self).__init__('Could not find baseline for this build')


class RpmdiffTestResult(TestResult):
    # pylint: disable=too-few-public-methods

    def __init__(self, runinfo, test_type, **kwargs):
        overall_result = RPMDIFF_OVERALL_SCORE[runinfo['overall_score']['description']]

        ids = {
            'rpmdiff_run_id': runinfo['run_id'],
        }

        urls = {
            'rpmdiff_url': runinfo['web_url']
        }

        super(RpmdiffTestResult, self).__init__('rpmdiff-{}'.format(test_type),
                                                overall_result,
                                                ids=ids,
                                                urls=urls,
                                                **kwargs)


class CIRpmdiff(Module):
    """
    CI RPMdiff module

    This module schedules an RPMdiff run, waits until it is finished and reports
    results in results shared function.
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

    brew_task = None
    check_interval = 60
    max_timeout = 3600 * 4
    rpmdiff_cmd = None

    _result_class = None

    def sanity(self):
        utils.check_for_commands(REQUIRED_CMDS)

    @staticmethod
    def _run_command(command):
        try:
            return utils.run_command(command)
        except CICommandError as exc:
            raise CIError("Failure during 'rpmdiff-remote' execution: {}".format(exc.output.stderr))

    def _get_runinfo(self, run_id):
        # make sure run_id is a string here, as utils run_command requires it
        command = self.rpmdiff_cmd + ["runinfo", str(run_id)]

        blob = json.loads(CIRpmdiff._run_command(command).stdout)
        log_blob(self.debug, 'rpmdiff-remote runinfo returned', utils.format_dict(blob))

        return blob

    def _wait_until_finished(self, run_id):
        start_time = time.time()
        runinfo = self._get_runinfo(run_id)
        self.verbose("RPMdiff run [{}] state: {}".format(run_id, runinfo['overall_score']['description']))
        while runinfo['overall_score']['description'] in ['Running', 'Queued for test']:
            if (time.time() - start_time) > self.max_timeout:
                raise CIError("Waiting for RPMdiff results timed out ")
            time.sleep(self.check_interval)
            runinfo = self._get_runinfo(run_id)
        return runinfo

    def _run_rpmdiff(self, test_type, nvr_baseline=None):
        if self.brew_task.scratch:
            command = self.rpmdiff_cmd + ["schedule", str(self.brew_task.task_id)]
        else:
            command = self.rpmdiff_cmd + ["schedule", self.brew_task.nvr]

        if test_type == 'comparison':
            command += ["--baseline", nvr_baseline]

        blob = json.loads(CIRpmdiff._run_command(command).stdout)
        self.debug('rpmdiff-remote schedule returned:\n{}'.format(utils.format_dict(blob)))
        self.info("web url: {}".format(blob['web_url']))
        return self._wait_until_finished(str(blob['run_id']))

    def _publish_results(self, runinfo, test_type):
        if test_type == 'comparison':
            result_type = 'koji_build_pair'
            item = '{} {}'.format(self.brew_task.nvr, self.brew_task.latest)
        else:
            result_type = 'koji_build'
            item = self.brew_task.nvr

        # basic result data and overall result
        tests = [{
            'data': {
                'item': item,
                'type': result_type,
                'newnvr': self.brew_task.nvr,
                'oldnvr': self.brew_task.latest,
                'scratch': self.brew_task.scratch,
                'taskid': self.brew_task.task_id
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
                        'newnvr': self.brew_task.nvr,
                        'oldnvr': self.brew_task.latest,
                        'scratch': self.brew_task.scratch,
                        'taskid': self.brew_task.task_id
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
        if not self.has_shared("results"):
            raise CIError('Cannot refresh old results, shared function \'results\' does not exist')

        results = self.shared("results")
        for result in results:
            if result.test_type == 'rpmdiff' and result.ids['rpmdiff_run_id'] == run_id:
                results.remove(result)
        self._publish_results(self._get_runinfo(run_id), self.option('type'))

    def execute(self):
        blacklist = self.option('blacklist')
        run_id = self.option('run-id')
        test_type = self.option('type')
        url = self.option('url')

        # override url if requested
        self.rpmdiff_cmd = ['rpmdiff-remote']
        if url:
            self.rpmdiff_cmd += ['--hub-url', url]

        # get a brew task instance
        self.brew_task = self.shared('brew_task')
        if self.brew_task is None:
            raise CIError('no brew build found, did you run brew module?')

        # blacklist packages
        if blacklist is not None:
            self.verbose('blacklisted packages: {}'.format(blacklist))
            if self.brew_task.component in blacklist.split(','):
                self.info('skipping blacklisted package {}'.format(self.brew_task.component))
                return

        if test_type == 'comparison':
            if self.brew_task.latest is None:
                self.warn('no baseline found, refusing to continue testing')
                return
            if self.brew_task.scratch is False and self.brew_task.latest == self.brew_task.nvr:
                self.info('cowardly refusing to compare same packages')
                return

        msg = ["running {} for task '{}'".format(test_type, self.brew_task.task_id)]
        msg += ['compared to {}'.format(self.brew_task.latest)] if test_type == 'comparison' else []
        self.info(' '.join(msg))

        if run_id:
            runinfo = self._get_runinfo(run_id)
        else:
            runinfo = self._run_rpmdiff(test_type, self.brew_task.latest)
        self.info('result: {}'.format(runinfo['overall_score']['description']))

        self._publish_results(runinfo, test_type)
