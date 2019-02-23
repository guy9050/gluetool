import itertools

import gluetool
from gluetool.log import log_dict
from gluetool.utils import new_xml_element


class BeahXUnit(gluetool.Module):
    """
    Provides common xUnit serializer for testing results produced by Beaker or Restraint testing.
    It is used from ``serialize`` methods of these specific testing result classes.
    """

    name = 'beah-xunit'
    description = 'xUnit serializer for Beaker and Restraint testing results.'

    options = {
        'test-source-template': {
            'help': 'Template to render test source location.'
        }
    }

    shared_functions = ('beah_xunit_serialize',)

    def beah_xunit_serialize(self, test_suite, result, payload=None):
        # pylint: disable=no-self-use
        """
        Given ``testsuite`` XML element, it will fill it with data corresponding to given result.

        :param element test_suite: ``<testsuite/>`` XML element.
        :param libci.results.TestResult: Result to serialize into xUnit.
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

                # pylint: disable=bare-except
                except:
                    return url

        else:
            def _get_test_source_url(test_name):
                # pylint: disable=unused-argument

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

                if run['bkr_status'].lower() != 'completed':
                    new_xml_element('error', _parent=test_case, message='Test did not complete')

                elif run['bkr_result'].lower() not in ('pass', 'passed'):
                    new_xml_element('failure', _parent=test_case, message='Test failed')

                for name, environment in run.get('testing-environments', {}).iteritems():
                    testing_environment = new_xml_element('testing-environment', _parent=test_case, name=name)

                    for field, value in environment.iteritems():
                        new_xml_element('property', _parent=testing_environment, name=field, value=value)

                # main_logs = [log for log in run['bkr_logs'] if log['name'].lower() in ('taskout.log',)]
                # if main_logs:
                #    output = gluetool.utils.run_command(['curl', '-s', '-u', ':', '--negotiate',
                # log['href'].encode('utf-8')])

                #    system_out = new_xml_element('system-out', _parent=test_case)
                #    system_out.string = output.stdout

        test_suite['tests'] = cnt_tests

        return test_suite
