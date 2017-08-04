import os

from libci import CIError
from libci.utils import cached_property, format_dict


class DispatchJenkinsJobMixin(object):
    """
    Base class providing common functionality to modules whose only goal is to accept
    options (from command-line or environment), and dispatch specific Jenkins job for
    a given task.

    This class brings only pieces relevant to its purpose, it's up to its children to
    be based not just on this mixin class but on the :py:class:`libci.ci.Module` as well.

    .. note::

       Value of the ``--id`` option is, by default, first searched in the environment, at it is expected
       to be set by Jenkins' machinery, e.g. by the ``redhat-ci-plugin``'.

    .. note::

       This module dispatches a Jenkins job, therefore it requires other module to provide connection
       to a Jenkins instance via the shared function ``jenkins``.
    """

    # pylint: disable=unused-variable
    job_name = None
    """Name of the Jenkins job this module dispatches."""

    options = {
        'thread-id': {
            'help': 'Testing thread ID'
        },
        'id': {
            'help': 'Task ID. If environment variable ``id`` exists, it overrides the command-line option.',
            'type': int
        },
        'job-name': {
            'help': 'Jenkins job name. Use this option to override the default name.'
        },
        'pipeline-prepend': {
            'help': '``citool`` options that will be added at the beginning of the pipeline.'
        },
        'pipeline-append': {
            'help': '``citool`` options that will be added at the end of the pipeline.'
        },
        'notify-recipients-options': {
            'help': 'Additional options for ``notify-recipients`` module.',
            'action': 'append',
            'default': []
        },
        'notify-email-options': {
            'help': 'Additional options for ``notify-email`` module.'
        }
    }

    def sanity(self):
        # if there's `id` env var, use it
        self._config['id'] = os.environ.get('id', self._config.get('id'))

        if not self.option('id'):
            raise CIError('Task ID not specified')

    @cached_property
    def build_params(self):
        """
        Converts command-line options - and possibly other sources as well - to a build parameters, a dictionary
        that's passed to Jenkins, listing keys and values which form parameters of the triggered Jenkins build.

        :rtype: dict
        """

        notify_recipients_options = self.option('notify-recipients-options')
        if notify_recipients_options:
            notify_recipients_options = ' '.join(notify_recipients_options)

        else:
            notify_recipients_options = None

        return {
            'thread_id': self.option('thread-id'),
            'id': self.option('id'),
            'pipeline_prepend': self.option('pipeline-prepend'),
            'pipeline_append': self.option('pipeline-append'),
            'notify_recipients_options': notify_recipients_options,
            'notify_email_options': self.option('notify-email-options')
        }

    def _dispatch(self, job_name, build_params):
        """
        Invoke the Jenkins build.

        :param str job_name: name of the Jenkins job to invoke.
        :param dict build_params: build parameters.
        :raises libci.ci.CIError: when Jenkins connection is not available.
        """

        jenkins = self.shared('jenkins')
        if jenkins is None:
            raise CIError("Module requires Jenkins connection, provided e.g. by the 'jenkins' module")

        self.debug("invoking job '{}' with parameters:\n{}".format(job_name, format_dict(build_params)))
        jenkins[job_name].invoke(build_params=build_params)

        self.info("invoked job '{}' with given parameters".format(job_name))

    def execute(self):
        job_name = self.option('job-name') if self.option('job-name') else self.job_name

        self._dispatch(job_name, self.build_params)
