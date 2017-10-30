from libci.utils import cached_property, format_dict


class DispatchJenkinsJobMixin(object):
    """
    Base class providing common functionality to modules whose only goal is to accept
    options (from command-line or environment), and dispatch specific Jenkins job for
    a given task.

    This class brings only pieces relevant to its purpose, it's up to its children to
    be based not just on this mixin class but on the :py:class:`libci.ci.Module` as well.

    .. note::

       Value of the ``id`` parameter is read from the shared function ``primary_task``.

    .. note::

       This module dispatches a Jenkins job, therefore it requires other module to provide connection
       to a Jenkins instance via the shared function ``jenkins``.
    """

    # pylint: disable=unused-variable
    job_name = None
    """Name of the Jenkins job this module dispatches."""

    options = {
        'testing-thread-id': {
            'help': 'Testing thread ID'
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
        'pipeline-state-reporter-options': {
            'help': 'Additional options for ``pipeline-state-reporter`` module.'
        },
        'notify-recipients-options': {
            'help': 'Additional options for ``notify-recipients`` module.',
            'action': 'append',
            'default': []
        },
        'notify-email-options': {
            'help': 'Additional options for ``notify-email`` module.'
        },
        'timeout-duration': {
            'help': 'Kill the pipeline when this many seconds elapsed.'
        }
    }

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
            'testing_thread_id': self.option('testing-thread-id'),
            # note that id is kept here for legacy reasons for now, should be removed in the future
            'id': self.shared('primary_task').task_id,
            'task_id': self.shared('primary_task').task_id,
            'pipeline_prepend': self.option('pipeline-prepend'),
            'pipeline_append': self.option('pipeline-append'),
            'pipeline_state_reporter_options': self.option('pipeline-state-reporter-options'),
            'notify_recipients_options': notify_recipients_options,
            'notify_email_options': self.option('notify-email-options'),
            'timeout_duration': self.option('timeout-duration')
        }

    def _dispatch(self, job_name, build_params):
        """
        Invoke the Jenkins build.

        :param str job_name: name of the Jenkins job to invoke.
        :param dict build_params: build parameters.
        """

        jenkins = self.shared('jenkins')

        self.debug("invoking job '{}' with parameters:\n{}".format(job_name, format_dict(build_params)))
        jenkins[job_name].invoke(build_params=build_params)

        self.info("invoked job '{}' with given parameters".format(job_name))

    def execute(self):
        job_name = self.option('job-name') if self.option('job-name') else self.job_name

        self.require_shared('primary_task', 'jenkins')

        self._dispatch(job_name, self.build_params)
