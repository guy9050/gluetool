import itertools
import re

import gluetool
from gluetool.log import log_dict, log_xml
from gluetool.utils import new_xml_element


class BeahXUnit(gluetool.Module):
    """
    Provides common xUnit serializer for testing results produced by Beaker or Restraint testing.
    It is used from ``serialize`` methods of these specific testing result classes.
    """

    name = 'beah-xunit'
    description = 'xUnit serializer for Beaker and Restraint testing results.'

    options = {
        'enable-polarion': {
            'help': 'Make generated xUnit RH Polarion friendly.',
            'action': 'store_true'
        },
        'missing-caseid-tasks-list': {
            'help': 'Yaml file with list of tasks ignored if caseid not found. By default Sentry warning emitted.',
            'metavar': 'PATH'
         },
        'test-source-template': {
            'help': 'Template to render test source location.'
        }
    }

    shared_functions = ('beah_xunit_serialize',)

    @gluetool.utils.cached_property
    def missing_caseid_tasks_list(self):
        if not self.option('missing-caseid-tasks-list'):
            return []

        return gluetool.utils.load_yaml(gluetool.utils.normalize_path(self.option('missing-caseid-tasks-list')))

    def beah_xunit_serialize(self, test_suite, result, payload=None):
        """
        Given ``testsuite`` XML element, it will fill it with data corresponding to given result.

        :param element test_suite: ``<testsuite/>`` XML element.
        :param gluetool_modules.libs.results.TestResult: Result to serialize into xUnit.
        :param payload: if set, it is used instead of ``result.payload``. It is a workaround to avoid copy/paste
            of this code when used by ``test-schedule-report`` module.
        :returns: ``<testsuite/>`` element, originaly given as ``test_suite`` argument.
        """

        def _add_property(properties, name, value):
            return new_xml_element('property', _parent=properties, name='baseosci.{}'.format(name), value=value or '')

        def _add_param(params, value):
            return new_xml_element('parameter', _parent=params, value=value)

        def _add_log(logs, name, href):
            return new_xml_element('log', _parent=logs, name=name, href=href)

        def _add_package(packages, nvr):
            return new_xml_element('package', _parent=packages, nvr=nvr)

        def _sort_children(parent, key_getter):
            log_xml(self.verbose, 'before sorting', parent)

            sorted_children = sorted(parent.children, key=key_getter)

            for el in parent.children:
                el.extract()

            for el in sorted_children:
                parent.append(el)

            log_xml(self.verbose, 'after sorting', parent)

        def _get_polarion_case_id(bkr_params):
            if not self.option('enable-polarion'):
                return None

            try:
                # Extract tcms test case id from bkr_params, e.g. CASEID="578756"
                # For Polarion the test case id must be in form TC#{ID}
                case_id = filter(lambda param: param.startswith('CASEID='), bkr_params)[0]
                return 'TC#{}'.format(case_id.split('=')[1].strip('"'))

            except IndexError:
                if any(re.match(task, test_name) for task in self.missing_caseid_tasks_list):
                    self.debug("Expected that TCMS test case ID is missing for '{}'".format(test_name))
                else:
                    self.warn("Failed to find TCMS test case ID for '{}'".format(test_name), sentry=True)

            return None

        if self.option('test-source-template'):
            def _get_test_source_url(test_name):
                context = gluetool.utils.dict_update(
                    self.shared('eval_context'),
                    {
                        'TEST_NAME': test_name,
                        'TEST_NAME_PARTS': test_name.split('/')
                    }
                )

                url = gluetool.utils.render_template(
                    self.option('test-source-template'),
                    logger=self.logger,
                    **context
                )

                try:
                    return gluetool.utils.treat_url(url, logger=self.logger)

                except:  # noqa: E722  # do not use bare 'except'
                    return url

        else:
            def _get_test_source_url(test_name):
                return '<unknown test source URL>'

        payload = payload or result.payload

        log_dict(self.verbose, 'serialize result', payload)

        cnt_tests = 0

        # Every instance (run) for every test case in result's payload will become
        # a single <testcase/> element under <testsuite/>.
        for test_number, (test_name, runs) in enumerate(payload.iteritems()):
            log_dict(self.verbose, '#{}: {}'.format(test_number, test_name), runs)

            for run_number, run in enumerate(runs):
                log_dict(self.verbose, '#{}:#{}'.format(test_number, run_number), run)

                cnt_tests += 1

                test_case = new_xml_element('testcase', _parent=test_suite, name=test_name, time=run['bkr_duration'])
                test_case_properties = new_xml_element('properties', _parent=test_case)
                test_case_params = new_xml_element('parameters', _parent=test_case)
                test_case_logs = new_xml_element('logs', _parent=test_case)
                test_case_phases = new_xml_element('phases', _parent=test_case)
                test_case_packages = new_xml_element('packages', _parent=test_case)

                for name, value in (
                        ('arch', 'bkr_arch'),
                        ('distro', 'bkr_distro'),
                        ('host', 'bkr_host'),
                        ('variant', 'bkr_variant'),
                        ('beaker-version', 'bkr_version'),
                        ('connectable_host', 'connectable_host'),
                        ('recipe-id', 'bkr_recipe_id'),
                        ('task-id', 'bkr_task_id'),
                        ('result', 'bkr_result'),
                        ('status', 'bkr_status')
                ):
                    _add_property(test_case_properties, name, run[value])

                _add_property(test_case_properties, 'testcase.source.url', _get_test_source_url(test_name))

                case_id = _get_polarion_case_id(run['bkr_params'])
                if case_id:
                    # we are not using _add_property here as it adds 'baseosci.' prefix to name attribute
                    new_xml_element(
                        'property', _parent=test_case_properties,
                        name='polarion-testcase-id', value=case_id
                    )

                for param in run['bkr_params']:
                    _add_param(test_case_params, param)

                for log in run['bkr_logs']:
                    _add_log(test_case_logs, log['name'], log['href'])

                # We need to pick one of the logs as the "testcase log".
                # Find the first log entry with matching 'name', or None if there's no such entry.
                first_testcase_log = next(
                    itertools.ifilter(
                        lambda x: x['name'].lower() in ('testout.log', 'taskout.log'), run['bkr_logs']
                    ), None
                )

                if first_testcase_log is not None:
                    _add_log(test_case_logs, 'testcase.log', first_testcase_log['href'])

                for package in run['bkr_packages']:
                    _add_package(test_case_packages, package)

                for phase in run['bkr_phases']:
                    test_phase = new_xml_element('phase', _parent=test_case_phases, name=phase['name'],
                                                 result=phase['result'])
                    test_phase_logs = new_xml_element('logs', _parent=test_phase)

                    for log in phase['logs']:
                        _add_log(test_phase_logs, log['name'], log['href'])

                    _sort_children(test_phase_logs, lambda child: child.attrs['name'])

                if run['bkr_status'].lower() != 'completed':
                    new_xml_element('error', _parent=test_case, message='Test did not complete')

                elif run['bkr_result'].lower() not in ('pass', 'passed'):
                    new_xml_element('failure', _parent=test_case, message='Test failed')

                for name, environment in run.get('testing-environments', {}).iteritems():
                    testing_environment = new_xml_element('testing-environment', _parent=test_case, name=name)

                    for field, value in environment.iteritems():
                        new_xml_element('property', _parent=testing_environment, name=field, value=value)

                    _sort_children(testing_environment, lambda child: child.attrs['name'])

                _sort_children(test_case_properties, lambda child: child.attrs['name'])
                _sort_children(test_case_params, lambda child: child.attrs['value'])
                _sort_children(test_case_logs, lambda child: child.attrs['name'])
                _sort_children(test_case_packages, lambda child: child.attrs['nvr'])

                # We're not sorting testing-environments but we should. It may require wrapping them into their
                # own parent element.

                # main_logs = [log for log in run['bkr_logs'] if log['name'].lower() in ('taskout.log',)]
                # if main_logs:
                #    output = gluetool.utils.run_command(['curl', '-s', '-u', ':', '--negotiate',
                # log['href'].encode('utf-8')])

                #    system_out = new_xml_element('system-out', _parent=test_case)
                #    system_out.string = output.stdout

        test_suite['tests'] = cnt_tests

        return test_suite
