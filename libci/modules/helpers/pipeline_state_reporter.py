"""
**Error information** describes optional bits in the case the message wishes to report
an exceptional state of the pipeline:

* ``message`` (string): user-presentable description of the error.
* ``soft`` (bool): whether the error was soft or not. Soft errors are usualy fixable by the user.


**Artifact information** describes optional bits in the case the message can report details
of the Koji/Brew/etc. artifact triggering the pipeline:

* ``id`` (int): artifact ID.
* ``namespace`` (string): ID namespace - Koji, Copr, Docker, etc.
* ``nvr`` (string, optional): built package NVR.
* ``branch`` (string, optional): branch in git repository the artifact was build from.
* ``issuer`` (string, optional): user who started the artifact.
* ``scratch`` (bool, optional): whether the artifact represents a scratch build.


**The message** structure is following:

* ``state`` (string): describes the state pipeline entered. One of ``started``, ``scheduled``,
  ``finished``, ``error``
* ``category`` (string): category of testing implemented by the pipeline.
* ``thread-id`` (string, optional): testing thread ID, if known.
* ``test-overall-result`` (string, optional): what is the overall result of the testing performed by
  the pipeline. One of ``pass``, ``fail``, ``unknown``.
* ``test-results`` (string, optional): results of the testing, in xUnit format, compressed by :py:mod:`zlib`
  library, and base64-encoded.
* ``note`` (string, optional): custom note with some relation towards the message.
* ``error`` (`Error information`, optional): error information.
* ``artifact`` (`Artifact information`, optional): artifact information.


.. code-block:: json

   {
       "category": "tier1",
       "state": "started",
       "thread-id": "d6c5958237fc"
   }

   {
       "category": "tier1",
       "state": "finished",
       "test-overall-result": "pass",
       "test-results": "eJzt...5BGb+g==",
       "thread-id": "d6c5958237fc"
   }
"""

import base64
import UserDict
import zlib

import libci


class Message(UserDict.UserDict):
    def __init__(self, module, **kwargs):
        UserDict.UserDict.__init__(self, **kwargs)

        self.module = module

    def serialize(self):
        return dict(self)


class PipelineStateReporter(libci.Module):
    """
    Sends messages reporting the pipeline state.

    Module sends two messages:

        * the first when the module is executed, reporting the pipeline just started. Depending
          on the module position in the pipeline, there were definitely actions taken before sending
          this message.
          This message can be disabled by ``--dont-report-started`` option.

        * the second message is sent when the pipeline is being destroyed. it can contain information
          about the error causing pipeline to crash, or export testing results.
    """

    name = 'pipeline-state-reporter'
    description = 'Sends messages reporting the pipeline state.'

    options = {
        'category': {
            'help': 'Sets ``category`` field in the messages. One of those passed to ``--categories`` option.',
            'type': str
        },
        'categories': {
            'help': 'Comma-separated list of available categories.'
        },
        'dont-report-started': {
            'help': 'Do not send out a "started" message automatically.',
            'action': 'store_true',
            'default': False
        },
        'bus-topic': {
            'help': 'Topic of the messages sent to the message bus.'
        }
    }

    required_options = ('categories', 'category', 'bus-topic')

    shared_functions = ('report_pipeline_state',)

    @libci.utils.cached_property
    def categories(self):
        return [s.strip() for s in self.option('categories').split(',')]

    def report_pipeline_state(self, state, thread_id=None, category=None,
                              test_overall_result=None, test_results=None, note=None,
                              artifact=None, error_soft=None, error_message=None):
        # pylint: disable=too-many-arguments
        """
        Send out the message reporting the pipeline state.

        If the argument is not set, its field won't be part of the message, with the exception
        of ``thread-id`` and ``artifact`` where shared functions could be called, if available.

        :param str state: State of the pipeline.
        :param str thread_id: The thread ID of the pipeline. If not set, shared function ``thread_id``
            can be used to provide the ID.
        :param str category: Pipeline category (``tier1``, ``covscan``, ...).
        :param str test_overall_result: Overall test result (``pass``, ``fail``, ``unknown``, ...).
        :param str note: Optional note.
        :param dict artifact: Task information. If not set, shared function ``primary_task`` can be used
            to provide the details.
        :param bool error_soft: Whether the reported error is soft or not.
        :param str error_message: Error message which can be presented to the common user.
        """

        message = Message(self, state=state)

        message['category'] = category or self.option('category')

        if test_overall_result is not None:
            message['test-overall-result'] = test_overall_result

        if test_results is not None:
            serialized = self.shared('serialize_results', 'xunit', test_results)
            compressed = zlib.compress(str(serialized))
            encoded = base64.b64encode(compressed)

            message['test-results'] = encoded

        if note is not None:
            message['note'] = note

        if error_soft is not None or error_message is not None:
            message['error'] = {
                'soft': error_soft,
                'message': error_message
            }

        if thread_id is not None:
            message['thread-id'] = thread_id

        elif self.has_shared('thread_id'):
            message['thread-id'] = self.shared('thread_id')

        if artifact is not None:
            message['artifact'] = artifact

        elif self.has_shared('primary_task'):
            task = self.shared('primary_task')

            message['artifact'] = {
                'id': task.task_id,
                'namespace': task.ARTIFACT_NAMESPACE,
                'nvr': task.nvr,
                'branch': task.branch,
                'issuer': task.issuer,
                'scratch': task.scratch
            }

        libci.log.log_dict(self.debug, 'pipeline state', message.serialize())

        if not self.has_shared('publish_bus_messages'):
            return

        message = libci.utils.Bunch(headers={}, body=message.serialize())

        self.shared('publish_bus_messages', message, topic=self.option('bus-topic'))

    def sanity(self):
        if not self.option('categories') or not self.option('category'):
            # both are required, and core will catch at least one of them is missing
            return

        if self.option('category') not in self.categories:
            raise libci.CIError("Unknown category '{}'".format(self.option('category')))

    def execute(self):
        if self.option('dont-report-started'):
            return

        self.info('reporting pipeline beginning')

        self.report_pipeline_state('started')

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
            self.report_pipeline_state('finished', test_overall_result=self._get_test_result(),
                                       test_results=self.shared('results'))
            return

        self.report_pipeline_state('error', error_soft=failure.soft, error_message=str(failure.exc_info[1].message))
