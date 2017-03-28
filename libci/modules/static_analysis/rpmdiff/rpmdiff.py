import json
import os
import re
import time
from libci import Module
from libci import CIError, CICommandError
from libci import utils

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
    1: "INFO",
    2: "INFO",
    3: "NEEDS_INSPECTION",
    4: "FAILED",
}

# required commands of module
REQUIRED_CMDS = ['rpmdiff-remote']


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
    shared_functions = ['results']

    brew_task = None
    check_interval = 60
    max_timeout = 3600 * 4
    rpmdiff_cmd = None
    _results = []

    def sanity(self):
        utils.check_for_commands(REQUIRED_CMDS)

    def results(self):
        return self._results

    @staticmethod
    def _parse_run_id(string):
        # parse JSON once BZ#1405962 is fixed
        match = re.search(r"u'run_id': (\d+)", string)
        if not match:
            msg = "could not find rpmdiff run id in rpmdiff-remote output"
            raise CIError(msg)
        return match.group(1)

    @staticmethod
    def _parse_web_url(string):
        # parse JSON once BZ#1405962 is fixed
        match = re.search(r"u'web_url': u'([^'']*)", string)
        if not match:
            msg = "could not find rpmdiff web url in rpmdiff-remote output"
            raise CIError(msg)
        return match.group(1)

    @staticmethod
    def _run_command(command):
        try:
            return utils.run_command(command)
        except CICommandError as exc:
            raise CIError("Failure during 'rpmdiff-remote' execution: {}".format(exc.output.stderr))

    def _get_runinfo(self, run_id):
        command = self.rpmdiff_cmd + ["runinfo", run_id]

        blob = json.loads(CIRpmdiff._run_command(command).stdout)
        utils.log_blob(self.debug, 'rpmdiff-remote runinfo returned', utils.format_dict(blob))

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

        out = CIRpmdiff._run_command(command).stdout
        # once we have valid JSON, we can parse that here
        # https://bugzilla.redhat.com/show_bug.cgi?id=1405962
        self.info("web url: {}".format(self._parse_web_url(out)))
        run_id = self._parse_run_id(out)
        return self._wait_until_finished(run_id)

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
                raise CIError('could not find baseline for this build')
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

        if test_type == 'comparison':
            result_type = 'koji_build_pair'
            item = '{} {}'.format(self.brew_task.nvr, self.brew_task.latest)
        else:
            result_type = 'koji_build'
            item = self.brew_task.nvr

        # basic result data and overall result
        result = {
            'type': 'rpmdiff',
            'result': RPMDIFF_OVERALL_SCORE[runinfo['overall_score']['description']],
            'urls': {
                'rpmdiff_url': runinfo['web_url'],
            },
            'rpmdiff': [{
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
        }

        # add jenkins job url
        if 'BUILD_URL' in os.environ:
            result['urls']['jenkins_job'] = os.environ['BUILD_URL']

        def _parse_results(data):
            parsed_results = []
            for result in data['results']:
                description = re.sub('[^a-z]', '_', result['test']['description'].lower())
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

        # add all runtest results
        result['rpmdiff'].extend(_parse_results(self._get_runinfo('{}/results'.format(runinfo['run_id']))))
        self.debug("results dictionary\n{}".format(utils.format_dict(result)))

        # publish it
        self._results = self.shared('results') or []
        self._results.append(result)
