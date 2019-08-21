import bs4

import gluetool
from gluetool.utils import cached_property, normalize_path_option


class BeakerJobXML(gluetool.Module):
    """
    Load static Beaker job descriptions (in XML).

    Instead of generating such job XML dynamically, e.g. by running tools like ``workflow-tomorrow``,
    this module can load static XML from prepared file(s).
    """

    name = 'beaker-job-xml'
    description = 'Load static Beaker job descriptions (in XML).'

    options = {
        'job-file': {
            'help': 'File with a job description. Use mutliple times to specify multiple jobs (default: none).',
            'action': 'append',
            'default': []
        }
    }

    required_options = ('job-file',)

    shared_functions = ('beaker_job_xml',)

    supported_dryrun_level = gluetool.glue.DryRunLevels.ISOLATED

    @cached_property
    def _beaker_job_xmls(self):
        jobs = []

        for filepath in normalize_path_option(self.option('job-file')):
            with open(filepath) as f:
                jobs.append(bs4.BeautifulSoup(f, 'xml'))

        return jobs

    def beaker_job_xml(self, **kwargs):
        """
        Return XML descriptions of Beaker jobs.

        :rtype: list(XML)
        :returns: List of elements representing Beaker jobs provided by user, one for each ``--job-file`` used.
        """

        return self._beaker_job_xmls
