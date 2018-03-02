"""
Sends pipeline messages as specified in draft.

https://docs.google.com/document/d/16L5odC-B4L6iwb9dp8Ry0Xk5Sc49h9KvTHrG86fdfQM/edit?ts=5a2af73c
"""

import base64
import zlib

import gluetool
from gluetool.log import log_dict
from gluetool.utils import render_template, treat_url


STATE_QUEUED = 'queued'
STATE_RUNNING = 'running'
STATE_COMPLETE = 'complete'
STATE_ERROR = 'error'


class PipelineStateReporter(gluetool.Module):
    """
    Sends messages reporting the pipeline state.

    The module sends two messages:

        * the first when the module is executed, reporting the pipeline just started. Depending
          on the module position in the pipeline, there were definitely actions taken before sending
          this message.
          This message can be disabled by ``--dont-report-running`` option.

        * the second message is sent when the pipeline is being destroyed. it can contain information
          about the error causing pipeline to crash, or export testing results.

    **Eval context**

    * ``PIPELINE_TEST_TYPE``: Type of tests provided in this pipeline, e.g. 'tier1', 'rpmdiff-analysis', 'covscan',
      or any other string. The value of this variable is taken from the ``test-type`` option.

    * ``PIPELINE_TEST_CATEGORY``: Category of tests performed in this pipeline. See ``test-category`` option
      for possible choices.
    """

    name = 'pipeline-state-reporter'
    description = 'Sends messages reporting the pipeline state.'

    options = [
        ('CI team options', {
            'ci-name': {
                'help': "Human-readable name of the CI system, e.g. 'BaseOS CI'.",
            },
            'ci-team': {
                'help': "Human-readable name of the team running the testing, e.g. 'BaseOS QE'."
            },
            'ci-url': {
                'help': 'URL of the CI system.'
            },
            'ci-contact-email': {
                'help': 'Team or CI system contact e-mail.'
            },
            'ci-contact-irc': {
                'help': 'Team or CI system IRC channel.'
            }
        }),
        ('Test options', {
            'test-category': {
                # pylint: disable=line-too-long
                'help': "Category of tests performed in this pipeline. One of 'static-analysis', 'functional', 'integration' or 'validation'.",  # Ignore PEP8Bear
                'choices': ['static-analysis', 'functional', 'integration', 'validation']
            },
            'test-type': {
                'help': "Type of tests provided in this pipeline, e.g. 'tier1', 'rpmdiff-analysis' or 'covscan'."
            }
        }),
        ('Tweaks', {
            'label': {
                'help': 'Custom pipeline label, distinguishing the pipelines of the same type.',
                'default': None
            },
            'note': {
                'help': 'Custom, arbitrary note or comment.',
                'default': None
            }
        }),
        ('General options', {
            'dont-report-running': {
                'help': "Do not send out a 'running' message automatically.",
                'action': 'store_true',
                'default': False
            },
            'bus-topic': {
                'help': 'Topic of the messages sent to the message bus.'
            }
        })
    ]

    required_options = (
        'ci-name', 'ci-team', 'ci-url', 'ci-contact-email', 'ci-contact-irc',
        'bus-topic')

    shared_functions = ('report_pipeline_state',)

    @property
    def eval_context(self):
        """
        Provides informations about test type and category to evaluation context.

        :rtype: dict
        """

        return {
            # common for all artifact providers
            'PIPELINE_TEST_TYPE': self.option('test-type'),
            'PIPELINE_TEST_CATEGORY': self.option('test-category'),
        }

    def _artifact_info(self):
        self.require_shared('primary_task')

        task = self.shared('primary_task')

        return {
            'type': task.ARTIFACT_NAMESPACE,
            'id': str(task.task_id),
            'component': task.component,
            'issuer': task.issuer,
            'branch': task.branch,
            'nvr': task.nvr,
            'scratch': task.scratch,
            'source': task.source,
        }

    def _ci_info(self):
        return {
            'name': self.option('ci-name'),
            'team': self.option('ci-team'),
            'url': self.option('ci-url'),
            'email': self.option('ci-contact-email'),
            'irc': self.option('ci-contact-irc')
        }

    def _run_info(self):
        context = self.shared('eval_context')

        if not context.get('JENKINS_BUILD_URL', None):
            return {
                'url': None,
                'log': None,
                'debug': None,
                'rebuild': None
            }

        def _render_url(template):
            return treat_url(render_template(template, logger=self.logger, **context))

        return {
            'url': _render_url('{{ JENKINS_BUILD_URL }}'),
            'log': _render_url('{{ JENKINS_BUILD_URL }}/console'),
            'debug': _render_url('{{ JENKINS_BUILD_URL }}/artifact/citool-debug.txt'),
            'rebuild': _render_url('{{ JENKINS_BUILD_URL }}/rebuild/parameterized')
        }

    def _init_message(self, test_category, test_type, thread_id):
        headers = {}
        body = {}

        artifact = self._artifact_info()
        ci = self._ci_info()
        run = self._run_info()

        headers.update(artifact)

        body['ci'] = ci
        body['run'] = run
        body['artifact'] = artifact

        body['type'] = test_type or self.option('test-type')
        body['category'] = test_category or self.option('test-category')
        body['label'] = self.option('label')
        body['note'] = self.option('note')

        if thread_id is not None:
            body['thread_id'] = thread_id

        elif self.has_shared('thread_id'):
            body['thread_id'] = self.shared('thread_id')

        return headers, body

    def report_pipeline_state(self, state, thread_id=None, topic=None,
                              test_category=None, test_type=None, test_overall_result=None, test_results=None,
                              distros=None,
                              error_message=None, error_url=None):
        # pylint: disable=too-many-arguments
        """
        Send out the message reporting the pipeline state.

        If the argument is not set, its field won't be part of the message, with the exception
        of ``thread-id`` and ``artifact`` where shared functions could be called, if available.

        :param str state: State of the pipeline.
        :param str topic: Message bus topic to report to. If not set, ``bus-topic`` option is used.
        :param str test_category: Pipeline category - ``functional``, ``static-analysis``, etc.
        :param str test_type: Pipeline type - ``tier1``, ``rpmdiff-analysis``, etc.
        :param str thread_id: The thread ID of the pipeline. If not set, shared function ``thread_id``
            is used to provide the ID.
        :param list(tuple(str, str, str)) distros: List of distros used by the systems in the testing
            process. Each item is a tuple of three strings:

            * ``label`` - arbitrary label of the system, e.g. ``master`` or ``client``.
            * ``os`` - identification of used distro, e.g. beaker distro name or OpenStack image name.
            * ``provider`` - what service provided the system, e.g. ``beaker`` or ``openstack``.
        :param str test_overall_result: Overall test result (``pass``, ``fail``, ``unknown``, ...).
        :param test_results: Internal representation of gathered testing results. If provided,
            it is serialized into the message.
        :param str error_message: Error message which can be presented to the common user.
        :param str error_url: URL of the issue in a tracking system which tracks the error. For example,
            link to an automatically created Sentry issue, or link to a Jira issue discissing the error.
        """

        distros = distros or []
        topic = topic or self.option('bus-topic')

        headers, body = self._init_message(test_category, test_type, thread_id)

        if state == STATE_QUEUED:
            pass

        elif state == STATE_RUNNING:
            pass

        elif state == STATE_COMPLETE:
            body['system'] = [
                {
                    'label': label,
                    'os': distro,
                    'provider': provider
                } for label, distro, provider in distros
            ]

            body['status'] = test_overall_result

            if test_results is not None:
                serialized = self.shared('serialize_results', 'xunit', test_results)
                compressed = zlib.compress(str(serialized))
                body['xunit'] = base64.b64encode(compressed)

            if self.has_shared('notification_recipients'):
                body['recipients'] = self.shared('notification_recipients')

        elif state == STATE_ERROR:
            body['reason'] = error_message
            body['issue_url'] = error_url

        render_context = gluetool.utils.dict_update(self.shared('eval_context'), {
            'HEADERS': headers,
            'BODY': body,
            'STATE': state
        })

        topic = gluetool.utils.render_template(topic, logger=self.logger, **render_context)

        self.debug("topic: '{}'".format(topic))
        log_dict(self.debug, 'pipeline state headers', headers)
        log_dict(self.debug, 'pipeline state body', body)

        if not self.has_shared('publish_bus_messages'):
            return

        message = gluetool.utils.Bunch(headers=headers, body=body)

        self.shared('publish_bus_messages', message, topic=topic)

    def execute(self):
        if self.option('dont-report-running'):
            return

        self.info('reporting pipeline beginning')

        self.report_pipeline_state('running')

    def _get_test_result(self):
        if not self.has_shared('results'):
            return 'unknown'

        results = self.shared('results')

        if all([result.overall_result.lower() in ('pass', 'passed') for result in results]):
            return 'pass'

        return 'fail'

    def destroy(self, failure=None):
        if failure is not None and isinstance(failure.exc_info[1], SystemExit):
            return

        self.info('reporting pipeline final state')

        if failure is None:
            self.report_pipeline_state(STATE_COMPLETE, test_overall_result=self._get_test_result(),
                                       test_results=self.shared('results'))
            return

        self.report_pipeline_state(STATE_ERROR, error_message=str(failure.exc_info[1].message),
                                   error_url=failure.sentry_event_url)
