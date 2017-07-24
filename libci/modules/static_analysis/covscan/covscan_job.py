from libci import Module
from libci.dispatch_job import DispatchJenkinsJobMixin


class CovscanJob(DispatchJenkinsJobMixin, Module):
    """
    Jenkins job module dispatching Covscan testing, as defined in ``ci-covscan.yaml`` file.

    .. note::

       Value of the ``--id`` option is, by default, first searched in the environment, at it is expected
       to be set by Jenkins' machinery, e.g. by the ``redhat-ci-plugin``.

    .. note::

       This module dispatches a Jenkins job, therefore it requires other module to provide connection
       to a Jenkins instance via the shared function ``jenkins``.
    """

    name = 'covscan-job'
    description = 'Create and run covscan job'

    job_name = 'ci-covscan'
