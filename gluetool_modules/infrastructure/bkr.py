import json
import os
import tempfile

import gluetool
from gluetool import GlueError, GlueCommandError
from libci.sentry import PrimaryTaskFingerprintsMixin


class BeakerError(PrimaryTaskFingerprintsMixin, GlueError):
    """
    When task context is not available, this exception is used instead of GlueError,
    to carry info on the task, which is something plain GlueError cannot do.
    """


class InvalidTasksError(PrimaryTaskFingerprintsMixin, GlueError):
    def __init__(self, task, test_tasks):
        super(InvalidTasksError, self).__init__(task, 'Invalid task names provided')

        self.tasks = test_tasks


class Beaker(gluetool.Module):
    """
    Provides a wrapper of `bkr` command, allowing for easier integration with other modules.
    """

    name = 'bkr'
    description = 'Wrapper of (low-level) Beaker API and commands.'

    shared_functions = ('submit_beaker_jobs',)

    def _submit_job(self, job):
        """
        Submit a single Beaker job.

        :param xml jobs: A job to submit.
        :rtype: list(int)
        :returns: List of Beaker job IDs.
        """

        gluetool.log.log_xml(self.debug, 'job to submit', job)

        # Save the job description. This method can (and will...) be called from multiple
        # thread, therefore we have to make sure we're using some sort of unique filename.
        with tempfile.NamedTemporaryFile(prefix='beaker-job-', suffix='.xml',
                                         dir=os.getcwd(), delete=False) as job_file:
            job_file.write(job.prettify(encoding='utf-8'))
            job_file.flush()

        # submit the job to beaker
        try:
            output = gluetool.utils.Command(['bkr'], options=['job-submit', job_file.name], logger=self.logger).run()

        except GlueCommandError as exc:
            if 'Invalid task(s):' in exc.output.stderr:
                s = exc.output.stderr.strip()
                invalid_tasks = [
                    name.strip() for name in s[s.index('Invalid task(s)') + 17:-2].split(',')
                ]

                raise InvalidTasksError(self.shared('primary_task'), invalid_tasks)

            raise BeakerError(self.shared('primary_task'),
                              "Failure during 'job-submit' execution: {}".format(exc.output.stderr))

        try:
            # Submitted: ['J:1806666', 'J:1806667']
            jobs = output.stdout[output.stdout.index(' ') + 1:]

            # ['J:1806666', 'J:1806667']
            jobs = jobs.replace('\'', '"')

            # ["J:1806666", "J:1806667"]
            jobs = json.loads(jobs)

            # ['J:1806666', 'J:1806667']
            ids = [int(job_id.split(':')[1]) for job_id in jobs]

            # [1806666, 1806667]

        except Exception as exc:
            raise BeakerError(self.shared('primary_task'),
                              'Cannot convert job-submit output to job ID: {}'.format(exc))

        return ids

    def submit_beaker_jobs(self, jobs):
        # pylint: disable=no-self-use
        """
        Submit jobs to Beaker.

        :param list(xml) jobs: List of jobs to submit.
        :rtype: list(int)
        :returns: List of Beaker job IDs.
        """

        beaker_ids = sum([
            self._submit_job(job) for job in jobs
        ], [])

        gluetool.log.log_dict(self.debug, 'beaker job IDs', beaker_ids)

        return beaker_ids
