import gluetool

from gluetool_modules.libs.test_schedule import TestSchedule, TestScheduleResult, TestScheduleEntryStage, \
    TestScheduleEntryState

# Type annotations
from typing import cast, TYPE_CHECKING, Any, Dict, List  # noqa

if TYPE_CHECKING:
    import bs4  # noqa


class TestScheduleReport(gluetool.Module):
    """
    Report test results, carried by schedule entries, and prepare serialized version of these results
    in a form of xUnit document.

    Optionally, make the xunit polarion friendly.
    """

    name = 'test-schedule-report'

    options = [
        ('General Options', {
            'overall-result-map': {
                'help': """
                        Instructions for overruling the default decision on the overall schedule result
                        (default: none).
                        """,
                'action': 'append',
                'default': [],
                'metavar': 'FILE'
            },
            'xunit-file': {
                'help': 'File to save the results into, in an xUnit format (default: %(default)s).',
                'action': 'store',
                'default': None,
                'metavar': 'FILE'
            }
        }),
        ('Polarion Options', {
            'enable-polarion': {
                'help': 'Make the xUnit RH Polarion friendly.',
                'action': 'store_true'
            },
            'polarion-lookup-method': {
                'help': 'Polarion lookup method.'
            },
            'polarion-lookup-method-field-id': {
                'help': 'Polarion lookup method field id.'
            },
            'polarion-project-id': {
                'help': 'Polarion project ID to use.'
            }
        })
    ]

    shared_functions = ['test_schedule_results', 'results']

    def __init__(self, *args, **kwargs):
        # type: (*Any, **Any) -> None

        super(TestScheduleReport, self).__init__(*args, **kwargs)

        self._result = None  # type: bs4.element.Tag

    def sanity(self):
        # type: () -> None
        required_polarion_options = [
            'polarion-project-id',
            'polarion-lookup-method',
            'polarion-lookup-method-field-id'
        ]

        if self.option('enable-polarion') and not all(self.option(option) for option in required_polarion_options):
            raise gluetool.GlueError('missing required options for Polarion.')

        if not self.option('enable-polarion') and any(self.option(option) for option in required_polarion_options):
            self.warn("polarion options have no effect because 'enable-polarion' was not specified.")

    @gluetool.utils.cached_property
    def _overall_result_instructions(self):
        # type: () -> List[Dict[str, Any]]

        instructions = []  # type: List[Dict[str, Any]]

        for filepath in gluetool.utils.normalize_path_option(self.option('overall-result-map')):
            instructions += gluetool.utils.load_yaml(filepath, logger=self.logger)

        return instructions

    @property
    def _schedule(self):
        # type: () -> TestSchedule

        return cast(
            TestSchedule,
            self.shared('test_schedule') or []
        )

    def _overall_result_base(self, schedule):
        # type: (TestSchedule) -> None
        """
        Find out overall result of the schedule.

        1. if any entry is still incomplete, result is ``UNDEFINED``
        2. if any entry finished didn't finish with ``OK`` state, result is ``ERROR``
        3. if all entries finished with ``PASSED`` result, result is ``PASSED``
        4. result of the first entry with non-``PASSED`` result is returned
        """

        if not all((schedule_entry.stage == TestScheduleEntryStage.COMPLETE for schedule_entry in schedule)):
            schedule.result = TestScheduleResult.UNDEFINED
            return

        if not all((schedule_entry.state == TestScheduleEntryState.OK for schedule_entry in schedule)):
            schedule.result = TestScheduleResult.ERROR
            return

        if all((schedule_entry.result == TestScheduleResult.PASSED for schedule_entry in schedule)):
            schedule.result = TestScheduleResult.PASSED
            return

        for schedule_entry in schedule:
            if schedule_entry.result == TestScheduleResult.PASSED:
                continue

            schedule.result = schedule_entry.result
            return

        schedule.result = TestScheduleResult.UNDEFINED

    def _overall_result_custom(self, schedule):
        # type: (TestSchedule) -> None
        """
        Return overall result of the schedule, influenced by instructions provided by the user.
        """

        if not self._overall_result_instructions:
            return

        context = gluetool.utils.dict_update(
            self.shared('eval_context'),
            {
                'SCHEDULE': schedule,
                'CURRENT_RESULT': schedule.result,
                'Results': TestScheduleResult
            }
        )

        def _set_result(instruction, command, argument, context):
            # type: (Dict[str, Any], str, Any, Dict[str, Any]) -> None

            result_name = argument.toupper()
            result_value = TestScheduleResult.__members__.get(result_name, None)

            if result_value is None:
                raise gluetool.GlueError("Unkown result '{}' requested by configuration".format(result_name))

            schedule.result = result_value

        self.shared('evaluate_instructions', self._overall_result_instructions, {
            'set-result': _set_result
        }, context=context)

    def _overall_result(self, schedule):
        # type: (TestSchedule) -> TestScheduleResult

        self._overall_result_base(schedule)
        self.debug('base overall result: {}'.format(schedule.result.name))

        self._overall_result_custom(schedule)
        self.debug('custom overall result: {}'.format(schedule.result.name))

        return schedule.result

    def _report_final_result(self, schedule):
        # type: (TestSchedule) -> None

        result = self._overall_result(schedule)

        if result == TestScheduleResult.PASSED:
            self.info('Result of testing: PASSED')

        elif result == TestScheduleResult.FAILED:
            self.error('Result of testing: FAILED')

        else:
            self.warn('Result of testing: {}'.format(result.name))

    def _serialize_results(self, schedule):
        # type: (TestSchedule) -> None

        test_suites = gluetool.utils.new_xml_element('testsuites')
        test_suites['overall-result'] = self._overall_result(schedule).name.lower()

        testsuites_properties = gluetool.utils.new_xml_element('properties', _parent=test_suites)

        if self.option('enable-polarion'):
            # we use custom lookup method with Test Case ID as test id in Polarion
            gluetool.utils.new_xml_element(
                'property', _parent=testsuites_properties,
                name='polarion-lookup-method', value=self.option('polarion-lookup-method')
            )
            gluetool.utils.new_xml_element(
                'property', _parent=testsuites_properties,
                name='polarion-custom-lookup-method-field-id', value=self.option('polarion-lookup-method-field-id')
            )

            gluetool.utils.new_xml_element(
                'property', _parent=testsuites_properties,
                name='polarion-project-id', value=self.option('polarion-project-id')
            )

            if self.shared('thread_id'):
                gluetool.utils.new_xml_element(
                    'property', _parent=testsuites_properties,
                    name='polarion-testrun-id', value=self.shared('thread_id')
                )

        test_suite = gluetool.utils.new_xml_element('testsuite', _parent=test_suites, tests='0')
        test_suite['overall-result'] = self._overall_result(schedule).name.lower()

        testsuite_properties = gluetool.utils.new_xml_element('properties', _parent=test_suite)

        if self.shared('thread_id'):
            gluetool.utils.new_xml_element(
                'property', _parent=testsuite_properties,
                name='baseosci.id.testing-thread', value=self.shared('thread_id')
            )

        # When adding new property, keep them sorted by the property name.
        gluetool.utils.new_xml_element(
            'property', _parent=testsuite_properties,
            name='baseosci.overall-result', value=schedule.result.name
        )

        for schedule_entry in schedule:
            self.shared('serialize_test_schedule_entry_results', schedule_entry, test_suite)

        self._result = test_suites

        gluetool.log.log_xml(self.debug, 'serialized results', self._result)

    def results(self):
        # type: () -> bs4.element.Tag

        return self._result

    def test_schedule_results(self):
        # type: () -> bs4.element.Tag

        return self._result

    def execute(self):
        # type: () -> None
        self.require_shared('test_schedule')

        self._serialize_results(self._schedule)
        self._report_final_result(self._schedule)

        if self.option('xunit-file'):
            with open(gluetool.utils.normalize_path(self.option('xunit-file')), 'w') as f:
                f.write(gluetool.log.format_xml(self._result))
                f.flush()

            self.info('results saved into {}'.format(self.option('xunit-file')))
