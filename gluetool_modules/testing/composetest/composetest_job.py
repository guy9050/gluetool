import gluetool
import libci.dispatch_job


class ComposeTestJob(libci.dispatch_job.DispatchJenkinsJobMixin, gluetool.Module):
    """
    Jenkins job module dispatching compose process testing, as defined in
    ``ci-composetest.yaml`` file.

    .. note::

       This module dispatches a Jenkins job, therefore it requires other module to provide connection
       to a Jenkins instance via the shared function ``jenkins``.
    """

    name = 'composetest-job'
    description = 'Job module dispatching compose process testing.'
    job_name = 'ci-composetest'
