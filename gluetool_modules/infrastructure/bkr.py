import collections
import json
import os
import stat
import tempfile
import urlparse

import bs4

import gluetool
from gluetool import GlueError, GlueCommandError
from gluetool.utils import Command
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


#: Represents (interesting) building pieces of Beaker matrix URL
#:
#: :ivar bool toggle_nacks_on: whether display of NACKed jobs is enabled.
#: :ivar list(int) job_ids: list of Beaker job IDs to display.
MatrixURL = collections.namedtuple('MatrixURL', ('toggle_nacks_on', 'job_ids'))


class Beaker(gluetool.Module):
    """
    Provides a wrapper of `bkr` command, allowing for easier integration with other modules.
    """

    name = 'bkr'
    description = 'Wrapper of (low-level) Beaker API and commands.'

    shared_functions = ('submit_beaker_jobs', 'beaker_jobs_results', 'parse_beaker_matrix_url')

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

        # Temporary file has limited permissions, but we'd like to make the file inspectable.
        os.chmod(job_file.name, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

        # submit the job to beaker
        try:
            output = Command(['bkr'], options=['job-submit', job_file.name], logger=self.logger).run()

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

    def beaker_jobs_results(self, jobs):
        """
        Find and return results of Beaker jobs. The "result" in question is an XML, returned
        by ``bkr job-results`` command.

        :param list(int) jobs: List of job IDs to query.
        :rtype: dict(int, xml)
        :returns: a mapping between job IDs (those given by in ``jobs`` argument) and their respective
            results.
        """

        # `bkr job-results` prints XML on its stdout - for each job, capture that text
        # and convert it to an XML tree.
        results = {
            job: bs4.BeautifulSoup(Command(['bkr'],
                                           options=['job-results', 'J:{}'.format(job)],
                                           logger=self.logger).run().stdout, 'xml')
            for job in jobs
        }

        for job, result in results.iteritems():
            gluetool.log.log_xml(self.verbose, 'job {} result'.format(job), result)

        return results

    def parse_beaker_matrix_url(self, url):
        """
        Split and normalize parts of Beaker matrix URL.

        :param str url: URL to process.
        :returns: An object with following properties:
            * ``toggle_nacks_on`` (``bool``) - whether display of NACKs was enabled
            * ``job_ids`` (``list(int)``) - list of job IDs to display
        """

        self.debug("parsing Beaker matrix URL '{}'".format(url))

        # parse matrix URL to its components (schema, hostname, port, query, ...)
        parsed = urlparse.urlparse(url)
        self.debug('  parsed: {}'.format(parsed))

        # extract "query" part of URL in a form of a mapping
        query = urlparse.parse_qs(parsed.query)
        gluetool.log.log_dict(self.debug, '  query', query)

        # `togle_nacks_on` value is a list with a single string item, 'on' or 'off'
        toggle_nacks_on = query.get('toggle_nacks_on', ['off'])[0].lower().strip() == 'on'
        self.debug('  toggle_nacks_on={}'.format(toggle_nacks_on))

        # job_ids is a list of all IDs packed into a single string, separated by a space
        job_ids = [
            int(job_id)
            for job_id in gluetool.utils.normalize_multistring_option(query.get('job_ids', []), separator=' ')
        ]
        gluetool.log.log_dict(self.debug, '  job_ids', job_ids)

        return MatrixURL(toggle_nacks_on=toggle_nacks_on, job_ids=job_ids)
