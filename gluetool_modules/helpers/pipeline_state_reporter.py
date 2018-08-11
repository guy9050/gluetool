"""
Sends pipeline messages as specified in draft.

https://docs.google.com/document/d/16L5odC-B4L6iwb9dp8Ry0Xk5Sc49h9KvTHrG86fdfQM/edit?ts=5a2af73c
"""

import base64
import datetime
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


    **Artifact details**

    Provided via ``--artifact-map`` option. Supports rules and their evaluation.

    .. code-block:: yaml

       ---

       # no "rule" key defaults to ``True``, meaning "always apply"
       - artifact-details:
           type: "{{ ARTIFACT_TYPE }}"

       - rule: ARTIFACT_TYPE == 'foo'
         artifact-details:
           id: "{{ PRIMARY_TASK.id }}"
           component: "{{ PRIMARY_TASK.component }}"
           issuer: "{{ PRIMARY_TASK.issuer }}"

       # Some details may be required to have different type, then use ``eval-as-rule: true`` flag
       # whose default is ``false``. Artifact details are then evaluated the same way rules are,
       # yielding possibly other data types than just string.
       - eval-as-rule: true
         artifact-details:
           branch: PRIMARY_TASK.branch or None  # string or None
           scratch: PRIMARY_TASK.scratch  # boolean

    **Final pipeline state**

    Provided via ``--final-state-map`` option, a mapping is used to determine the final state of the pipeline. By
    default, when exception was raised and failure is being handled, the final result is supposed to be ``error``,
    but on some occasions user might want to "whitelist" some of the errors.

    Rules are optional, with ``True`` being the default (i.e. no rule means the instruction applies always). The
    first instruction allowed by its rules wins, no other instructions are inspected.

    If there is no instruction map or no rule matched, the final state is determined easily - if there was an
    exception, it's ``error``, ``complete`` otherwise.

    Besides the common evaluation context, a ``FAILURE`` variable is available, representing
    the failure - if any - being the cause of the pipeline doom. If there was no failure, the
    variable is set to ``None``.

    .. code-block:: yaml

       ---

       # If there is a failure, and it's an exception we want to pretend like nothing happened, set the state.
       - rules: FAILURE and FAILURE.exc_info and FAILURE.exc_info[0].__name__ in ('ThisIsFineError',)
         state: complete

       # If there is a soft failure, pretend like nothing happened.
       - rules: FAILURE and FAILURE.soft
         state: complete

       # Final "catch the rest" instruction to set "complete" is not necessary
       # - state: complete
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
            'test-namespace': {
                'help':
                    """
                    Prefix to be used when constructing result testcase name in ResultsDB. The name is rendered
                    with these context variables available:

                        PRIMARY_TASK - object with ``primary_task`` shared function if available, None otherwise
                    """
            },
            'test-type': {
                'help': "Type of tests provided in this pipeline, e.g. 'tier1', 'rpmdiff-analysis' or 'covscan'."
            }
        }),
        ('Mapping options', {
            'artifact-map': {
                'help': "File with description of items provided as artifact info."
            },
            'final-state-map': {
                'help': 'Instructions to decide the final state of the pipeline.'
            }
        }),
        ('Tweaks', {
            'label': {
                'help': 'Custom pipeline label, distinguishing the pipelines of the same type (default: %(default)s).',
                'default': None
            },
            'note': {
                'help': 'Custom, arbitrary note or comment (default: %(default)s).',
                'default': None
            }
        }),
        ('General options', {
            'dont-report-running': {
                'help': "Do not send out a 'running' message automatically (default: %(default)s).",
                'action': 'store_true',
                'default': 'no'
            },
            'bus-topic': {
                'help': 'Topic of the messages sent to the message bus.'
            }
        })
    ]

    required_options = (
        'ci-name', 'ci-team', 'ci-url', 'ci-contact-email', 'ci-contact-irc',
        'bus-topic',
        'test-namespace')

    shared_functions = ('report_pipeline_state',)

    @property
    def eval_context(self):
        # pylint: disable=unused-variable
        __content__ = {  # noqa
            'PIPELINE_TEST_TYPE': """
                                  Type of tests provided in this pipeline, e.g. ``tier1``, ``rpmdiff-analysis``,
                                  ``covscan``, or any other string. The value of this variable is taken from the
                                  ``test-type`` option.
                                  """,
            'PIPELINE_TEST_CATEGORY': """
                                      Category of tests performed in this pipeline. See ``test-category`` option.
                                      """,
            'PIPELINE_TEST_NAMESPACE': """
                                       Test namespace (i.e. prefix) used when constructing ResultsDB testcase name.
                                       See ``test-namespace`` option.
                                       """,
        }

        return {
            # common for all artifact providers
            'PIPELINE_TEST_TYPE': self.option('test-type'),
            'PIPELINE_TEST_CATEGORY': self.option('test-category'),
            'PIPELINE_TEST_NAMESPACE': self._get_test_namespace()
        }

    @gluetool.utils.cached_property
    def artifact_map(self):
        if not self.option('artifact-map'):
            return []

        return gluetool.utils.load_yaml(self.option('artifact-map'), logger=self.logger)

    @gluetool.utils.cached_property
    def final_state_map(self):
        if not self.option('final-state-map'):
            return []

        return gluetool.utils.load_yaml(self.option('final-state-map'), logger=self.logger)

    def _artifact_info(self):
        self.require_shared('evaluate_instructions', 'evaluate_rules')

        artifact_info = {}

        # callback for 'artifact-details' command in artifact_map instructions, applies changes to artifact_info
        def _artifact_details_callback(instruction, command, argument, context):
            # pylint: disable=unused-argument

            if instruction.get('eval-as-rule', False):
                artifact_info.update({
                    detail: self.shared('evaluate_rules', value, context=context)
                    for detail, value in argument.iteritems()
                })

            else:
                artifact_info.update({
                    detail: render_template(value, **context) for detail, value in argument.iteritems()
                })

            log_dict(self.debug, 'artifact info', artifact_info)

        # callback for 'eval-as-rule' command in atifact_map instructions - it does nothing, this command
        # is handled by _artifact_details callback, but we must provide it anyway to make rules-engine happy.
        def _eval_as_rule_callback(instruction, command, argument, context):
            # pylint: disable=unused-argument

            pass

        self.shared('evaluate_instructions', self.artifact_map, {
            'artifact-details': _artifact_details_callback,
            'eval-as-rule': _eval_as_rule_callback
        })

        return artifact_info

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

    def _init_message(self, test_category, test_namespace, test_type, thread_id):
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
        body['namespace'] = test_namespace or self._get_test_namespace()

        body['generated_at'] = datetime.datetime.utcnow().isoformat(' ')

        if thread_id is not None:
            body['thread_id'] = thread_id

        elif self.has_shared('thread_id'):
            body['thread_id'] = self.shared('thread_id')

        return headers, body

    def report_pipeline_state(self, state, thread_id=None, topic=None,
                              test_category=None, test_namespace=None, test_type=None,
                              test_overall_result=None, test_results=None,
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
        :param str test_namespace: Test namespace, used to construct test case name in ResultsDB.
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

        headers, body = self._init_message(test_category, test_namespace, test_type, thread_id)

        if state == STATE_COMPLETE:
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

        # Send error properties in any case - despite the final state being e.g. 'complete',
        # an exception may have been raised and by always reporting the properties we can be
        # sure even the 'complete' report would be connected with the original issue, and
        # therefore open to investigation.
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

    def _get_test_namespace(self):
        """
        Return a rendered test namespace.

        Note that we cannot use the whole shared context here, as we would endup in a recursion, because this module
        provides the test namespace in the shared evaluation context as ``PIPELINE_TEST_NAMESPACE``.

        :returns: a rendered test namespace with one variable available in context.
        """
        try:
            context = {
                'PRIMARY_TASK': self.shared('primary_task')
            }

        except gluetool.glue.GlueError:
            # if no primary task available yet, render and empty dictionary
            context = {}

        return gluetool.utils.render_template(self.option('test-namespace'),
                                              logger=self.logger,
                                              **context)

    def _get_test_result(self):
        if not self.has_shared('results'):
            return 'unknown'

        results = self.shared('results')

        if all([result.overall_result.lower() in ('pass', 'passed') for result in results]):
            return 'passed'

        return 'failed'

    def _get_final_state(self, failure):
        """
        Read instructions from a file, and find out what the final state of the crrent pipeline
        should be.
        """

        context = gluetool.utils.dict_update(self.shared('eval_context'), {
            'FAILURE': failure
        })

        for instr in self.final_state_map:
            log_dict(self.debug, 'final state instruction', instr)

            if not self.shared('evaluate_rules', instr.get('rules', 'True'), context=context):
                self.debug('denied by rules')
                continue

            if 'state' not in instr:
                self.warn('Final state map matched but did not yield any state', sentry=True)
                continue

            self.debug("final state set to '{}'".format(instr['state']))

            return instr['state']

        return STATE_ERROR if failure else STATE_COMPLETE

    def destroy(self, failure=None):
        if failure is not None and isinstance(failure.exc_info[1], SystemExit):
            return

        self.info('reporting pipeline final state')

        kwargs = {
            'test_overall_result': self._get_test_result(),
            'test_results': self.shared('results')
        }

        if failure:
            kwargs.update({
                'error_message': str(failure.exc_info[1].message),
                'error_url': failure.sentry_event_url
            })

        self.report_pipeline_state(self._get_final_state(failure), **kwargs)
