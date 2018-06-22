import shlex

import gluetool
from gluetool import GlueError, SoftGlueError, GlueCommandError
from libci.sentry import PrimaryTaskFingerprintsMixin


class BeakerJobwatchError(PrimaryTaskFingerprintsMixin, GlueError):
    """
    Hard exception, used instead plain :py:class:`gluetool.GlueError` when task context
    is available and reasonable.
    """


class BeakerJobwatchAbortedError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task, matrix_url):
        super(BeakerJobwatchAbortedError, self).__init__(task, 'Beaker job(s) aborted, inform the user nicely')

        self.matrix_url = matrix_url


class BeakerJobwatch(gluetool.Module):
    name = 'beaker-jobwatch'
    description = 'Runs beaker-jobwatch to babysit given Beaker jobs.'

    options = {
        'jobwatch-options': {
            'help': 'Additional options for beaker-jobwatch'
        }
    }

    shared_functions = ('beaker_jobwatch',)

    def _get_matrix_url(self, jobwatch_log):
        """
        Returns beaker matrix url parsed from beaker-jobwatch's output.

        :param str jobwatch_log: Output of beaker-jobwatch.
        :returns: matrix url as a string
        :raises: GlueError if output is invalid, matrix url not found or not finished
        """

        # beaker-jobwatch output usually looks like this:
        #
        # Broken: 0
        # Running:   0/1
        # Completed: 1/1
        # 	TJ#1739067
        # https://beaker.engineering.redhat.com/matrix/?toggle_nacks_on=on&job_ids=1739067
        # duration: 3:39:03.805050
        # finished successfully

        jobwatch_log = jobwatch_log.strip().split('\n')

        if len(jobwatch_log) < 3:
            raise BeakerJobwatchError(self.shared('primary_task'), 'jobwatch output is unexpectedly short')

        if not jobwatch_log[-3].startswith('https://beaker.engineering.redhat.com/matrix/'):
            raise BeakerJobwatchError(self.shared('primary_task'),
                                      'Could not find beaker matrix URL in jobwatch output')

        self.info(jobwatch_log[-1].strip())
        if jobwatch_log[-1].strip() not in ['finished successfully', 'finished unsuccessfully']:
            raise BeakerJobwatchError(self.shared('primary_task'), 'beaker-jobwatch does not report completion')

        self.info('beaker-jobwatch finished')

        # matrix url is always on the 3rd line from the end
        return jobwatch_log[-3].strip()

    def beaker_jobwatch(self, jobs, end_task=None, critical_tasks=None):
        """
        Start beaker-jobwatch, to baby-sit given jobs, and wait for their completion.

        :param list(int) jobs: List of Beaker job IDs.
        :rtype: tuple(gluetool.utils.ProcessOutput, str)
        :returns: ``beaker-jobwatch`` output (as a :py:class:`gluetool.utils.ProcessOutput` instance)
            and the final Beaker matrix URL.
        """

        critical_tasks = critical_tasks or []

        cmd = gluetool.utils.Command(['beaker-jobwatch'], logger=self.logger, options=[
            '--skip-broken-machines'
        ])

        if self.option('jobwatch-options'):
            cmd.options += shlex.split(self.option('jobwatch-options'))

        for job_id in jobs:
            cmd.options += [
                '--job', str(job_id)
            ]

        if end_task:
            cmd.options += [
                '--end-task', end_task
            ]

        for task in critical_tasks:
            cmd.options += [
                '--critical-task', task
            ]

        self.info("running 'beaker-jobwatch' to babysit the jobs")

        try:
            output = cmd.run(inspect=True)

            return output, self._get_matrix_url(output.stdout)

        except GlueCommandError as exc:
            # If beaker-jobwatch runs unsuccessfuly, it exits with retcode 2
            # this most probably means that the jobs aborted
            if exc.output.exit_code == 2:
                matrix_url = self._get_matrix_url(exc.output.stdout)
                raise BeakerJobwatchAbortedError(self.shared('primary_task'), matrix_url)

            raise BeakerJobwatchError(self.shared('primary_task'),
                                      "Failure during 'jobwatch' execution: {}".format(exc.output.stderr))
