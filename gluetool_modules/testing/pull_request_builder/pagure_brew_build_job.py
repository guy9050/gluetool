import gluetool
import libci.dispatch_job


class BrewBuildJob(libci.dispatch_job.DispatchJenkinsJobMixin, gluetool.Module):
    """
    Jenkins job module dispatching brew build, as defined in ``ci-pagure-brew-build.yaml`` file.

    .. note::

       This module dispatches a Jenkins job, therefore it requires other module to provide connection
       to a Jenkins instance via the shared function ``jenkins``.
    """

    name = 'pagure-brew-build-job'
    description = 'Create and run pagure-brew-build job'

    job_name = 'ci-pagure-brew-build'
