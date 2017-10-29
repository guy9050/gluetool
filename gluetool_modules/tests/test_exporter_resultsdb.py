import pytest

from mock import MagicMock

from gluetool_modules.helpers.exporter_resultsdb import CIExporterResultsDB
from gluetool_modules.static_analysis.covscan.covscan import CovscanTestResult
from gluetool_modules.static_analysis.rpmdiff.rpmdiff import RpmdiffTestResult
from gluetool_modules.testing.beaker.beaker import BeakerTestResult
from gluetool_modules.testing.restraint.runner import RestraintTestResult
from libci.results import TestResult
from . import create_module, patch_shared, assert_shared


@pytest.fixture(name='module')
def fixture_module():
    ci, module = create_module(CIExporterResultsDB)

    # pylint: disable=protected-access
    module._config['topic-pattern'] = 'topic://dummy/topic/{category}/foo'

    return ci, module


def test_loadable(module):
    ci, _ = module
    # pylint: disable=protected-access
    python_mod = ci._load_python_module('helpers', 'exporter_resultsdb',
                                        'gluetool_modules/helpers/exporter_resultsdb.py')

    assert hasattr(python_mod, 'CIExporterResultsDB')


def test_unknown_type(log, module, monkeypatch):
    _, module = module
    result_type = 'unsupported_type'

    mock_publish = MagicMock()

    patch_shared(monkeypatch, module, {
        'results': [TestResult(module.glue, result_type, 'overall_results')],
        'publish_bus_messages': mock_publish
    })

    module.execute()

    assert log.records[-1].message == "skipping unsupported result type '{}'".format(result_type)
    mock_publish.assert_not_called()


def test_covscan_nobrew(module, monkeypatch):
    _, module = module
    result_type = 'covscan'

    patch_shared(monkeypatch, module, {
        'results': [TestResult(module.glue, result_type, 'overall_results')]
    })

    assert_shared('primary_task', module.execute)


def test_covscan(module, monkeypatch):
    _, module = module

    nvr = 'dummy_nvr'
    task_id = 'dummy_task_id'
    brew_url = 'dummy_brew_url'
    latest = 'dummy_baseline'
    covscan_url = 'dummy_covscan_url'
    overall_results = 'dummy_overall_results'

    mocked_task = MagicMock(nvr=nvr, scratch=True, task_id=task_id, url=brew_url, latest=latest)
    mocked_covscan_result = MagicMock(url=covscan_url, add=[], fixed=[])
    mocked_result = CovscanTestResult(module.glue, overall_results, mocked_covscan_result, mocked_task)
    mocked_publish = MagicMock()

    patch_shared(monkeypatch, module, {
        'primary_task': mocked_task,
        'results': [mocked_result, mocked_result]
    })

    module.glue.shared_functions['publish_bus_messages'] = mocked_publish

    module.execute()

    assert len(mocked_publish.mock_calls) == 4

    _, args, kwargs = mocked_publish.mock_calls[1]

    message1 = args[0]
    assert kwargs['topic'] == 'topic://dummy/topic/covscan/foo'

    _, args, kwargs = mocked_publish.mock_calls[3]

    message2 = args[0]
    assert kwargs['topic'] == 'topic://dummy/topic/covscan/foo'

    assert message1.headers['CI_TYPE'] == 'resultsdb'
    assert message1.headers['type'] == 'koji_build_pair'
    assert message1.headers['testcase'] == 'dist.covscan'
    assert message1.headers['scratch']
    assert message1.headers['taskid'] == task_id
    assert message1.headers['item'] == '{} {}'.format(nvr, latest)

    assert message2.headers['CI_TYPE'] == 'resultsdb'
    assert message2.headers['type'] == 'koji_build_pair'
    assert message2.headers['testcase'] == 'dist.covscan'
    assert message2.headers['scratch']
    assert message2.headers['taskid'] == task_id
    assert message2.headers['item'] == '{} {}'.format(nvr, latest)

    assert message1.body['data']['item'] == '{} {}'.format(nvr, latest)
    assert message1.body['data']['newnvr'] == nvr
    assert message1.body['data']['oldnvr'] == latest
    assert message1.body['data']['scratch']
    assert message1.body['data']['taskid'] == task_id
    assert message1.body['outcome'] == overall_results
    assert message1.body['ref_url'] == covscan_url

    assert message2.body['data']['item'] == '{} {}'.format(nvr, latest)
    assert message2.body['data']['newnvr'] == nvr
    assert message2.body['data']['oldnvr'] == latest
    assert message2.body['data']['scratch']
    assert message2.body['data']['taskid'] == task_id
    assert message2.body['outcome'] == overall_results
    assert message2.body['ref_url'] == covscan_url


def rpmdiff(result_type, topic, module, monkeypatch):
    _, module = module

    task_id = 'dummy_task_id'
    name = 'dummy_name'
    rpmdiff_type = 'dummy_type'
    item = 'dummy_item'
    scratch = True

    mocked_task = MagicMock(nvr='nvr', scratch=scratch, task_id=task_id, url='dummy_brew_url', latest='dummy_baseline')
    run_info = {}
    run_info['run_id'] = 'run_id'
    run_info['web_url'] = 'dummy_rpmdiff_url'
    run_info['overall_score'] = {}
    run_info['overall_score']['description'] = 'Passed'
    subresult = {'data': {}, 'testcase': {}}

    subresult['data']['type'] = rpmdiff_type
    subresult['testcase']['name'] = name
    subresult['data']['scratch'] = scratch
    subresult['data']['taskid'] = task_id
    subresult['data']['item'] = item

    mocked_result = RpmdiffTestResult(module.glue, run_info, result_type, payload=[subresult, subresult, subresult])

    patch_shared(monkeypatch, module, {
        'primary_task': mocked_task,
        'results': [mocked_result, mocked_result]
    })

    mocked_publish = MagicMock()
    module.glue.shared_functions['publish_bus_messages'] = mocked_publish

    module.execute()

    assert len(mocked_publish.mock_calls) == 12

    _, args, kwargs = mocked_publish.mock_calls[1]

    message = args[0]
    assert kwargs['topic'] == 'topic://dummy/topic/rpmdiff.{}/foo'.format(topic)

    assert message.headers['CI_TYPE'] == 'resultsdb'
    assert message.headers['type'] == rpmdiff_type
    assert message.headers['testcase'] == name
    assert message.headers['scratch']
    assert message.headers['taskid'] == task_id
    assert message.headers['item'] == item

    assert message.body == subresult


def test_rpmdiff_comparison(module, monkeypatch):
    rpmdiff('comparison', 'comparison', module, monkeypatch)


def test_rpmdiff_analysis(module, monkeypatch):
    rpmdiff('analysis', 'analysis', module, monkeypatch)


DUMMY_RUN1 = {
    'bkr_status': 'COMPLETED',
    'bkr_result': 'FAIL'
}

DUMMY_RUN2 = {
    'bkr_status': 'completed',
    'bkr_result': 'pass'
}

DUMMY_RUN3 = {
    'bkr_status': 'not_completed',
    'bkr_result': 'fail'
}

DUMMY_PAYLOAD = {
    'run1': [DUMMY_RUN1, DUMMY_RUN2],
    'run2': [DUMMY_RUN3, DUMMY_RUN1],
    'run3': [DUMMY_RUN2, DUMMY_RUN3]
}

EXECUTED = 4
FAILED = 2


def functional_testing(test_result, module, monkeypatch):
    _, module = module

    nvr = 'dummy_nvr'
    task_id = 'dummy_task_id'
    scratch = False
    distro = 'dummy_distro'
    component = 'dummy_component'
    target = 'dummy_target'
    result_type = test_result.test_type

    mocked_task = MagicMock(nvr=nvr, scratch=scratch, task_id=task_id, url='dummy_brew_url',
                            latest='dummy_baseline', component=component, target=target)

    patch_shared(monkeypatch, module, {
        'primary_task': mocked_task,
        'results': [test_result, test_result],
        'distro': distro,
        'notification_recipients': None
    })

    build_type = 'dummy_build_type'
    job_url = 'dummy_job_url'
    build_url = 'dummy_build_url'

    monkeypatch.setenv('BUILD_TYPE', build_type)
    monkeypatch.setenv('JOB_URL', job_url)
    monkeypatch.setenv('BUILD_URL', build_url)

    mocked_publish = MagicMock()
    module.glue.shared_functions['publish_bus_messages'] = mocked_publish

    module.execute()

    assert len(mocked_publish.mock_calls) == 4

    _, args, kwargs = mocked_publish.mock_calls[1]

    message = args[0]
    assert kwargs['topic'] == 'topic://dummy/topic/tier1/foo'

    assert message.headers['CI_TYPE'] == 'ci-metricsdata'
    assert message.headers['component'] == nvr
    assert message.headers['taskid'] == task_id

    assert message.body['component'] == nvr
    assert message.body['trigger'] == 'brew build'

    assert message.body['tests'][0]['executor'] == 'CI_OSP' if result_type == 'restraint' else 'beaker'
    assert message.body['tests'][0]['executed'] == EXECUTED
    assert message.body['tests'][0]['failed'] == FAILED

    assert message.body['base_distro'] == distro
    assert message.body['brew_task_id'] == task_id
    job_name = 'ci-{}-brew-{}-2-runtest'.format(component, target)
    assert message.body['job_name'] == job_name

    assert message.body['build_type'] == build_type
    assert message.body['jenkins_job_url'] == job_url
    assert message.body['jenkins_build_url'] == build_url
    assert message.body['build_number'] == 'unknown'
    assert message.body['CI_tier'] == 1
    assert message.body['team'] == 'baseos'
    # this need fix in other commit
    assert message.body['recipients'] == 'unknown'


def test_beaker(module, monkeypatch):
    ci, _ = module

    test_result = BeakerTestResult(ci, 'PASS', 'some_matrix', payload=DUMMY_PAYLOAD)
    functional_testing(test_result, module, monkeypatch)


def test_restraint(module, monkeypatch):
    ci, _ = module

    test_result = RestraintTestResult(ci, 'PASS', payload=DUMMY_PAYLOAD)
    functional_testing(test_result, module, monkeypatch)


def test_ci_metricsdata_no_brew(module):
    _, module = module

    assert_shared('primary_task', module.process_ci_metricsdata, 'dummy_result', 'dummy_result_type')


def test_ci_metricsdata_no_distro(module, monkeypatch):
    _, module = module

    patch_shared(monkeypatch, module, {
        'primary_task': None,
    })

    assert_shared('distro', module.process_ci_metricsdata, 'dummy_result', 'dummy_result_type')
