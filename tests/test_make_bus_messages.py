import pytest

from mock import MagicMock
from libci.modules.helpers.make_bus_messages import CIMakeBusMessages
from libci.results import TestResult
from libci.modules.static_analysis.covscan.covscan import CovscanTestResult
from libci.modules.static_analysis.rpmdiff.rpmdiff import RpmdiffTestResult
from libci.modules.testing.beaker.beaker import BeakerTestResult
from libci.modules.testing.restraint.runner import RestraintTestResult
from . import create_module, patch_shared, assert_shared


@pytest.fixture(name='module')
def fixture_module():
    return create_module(CIMakeBusMessages)


def test_loadable(module):
    ci, _ = module
    # pylint: disable=protected-access
    python_mod = ci._load_python_module('helpers', 'make_bus_messages',
                                        'libci/modules/helpers/make_bus_messages.py')

    assert hasattr(python_mod, 'CIMakeBusMessages')


def test_unknown_type(log, module, monkeypatch):
    _, module = module
    result_type = 'unsupported_type'

    patch_shared(monkeypatch, module, {
        'results': [TestResult(module.ci, result_type, 'overall_results')]
    })
    monkeypatch.setattr(module, 'store', MagicMock())

    module.execute()
    assert log.records[-1].message == "skipping unsupported result type '{}'".format(result_type)
    module.store.assert_not_called()


def test_covscan_nobrew(module, monkeypatch):
    _, module = module
    result_type = 'covscan'

    patch_shared(monkeypatch, module, {
        'results': [TestResult(module.ci, result_type, 'overall_results')]
    })

    monkeypatch.setattr(module, 'store', MagicMock())

    assert_shared('primary_task', module.execute)
    module.store.assert_not_called()


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
    mocked_result = CovscanTestResult(module.ci, overall_results, mocked_covscan_result, mocked_task)

    patch_shared(monkeypatch, module, {
        'primary_task': mocked_task,
        'results': [mocked_result, mocked_result]
    })

    module.execute()

    result = module.bus_messages()

    assert len(result) == 1
    assert len(result['covscan']) == 2

    assert result['covscan'][0].headers['CI_TYPE'] == 'resultsdb'
    assert result['covscan'][0].headers['type'] == 'koji_build_pair'
    assert result['covscan'][0].headers['testcase'] == 'dist.covscan'
    assert result['covscan'][0].headers['scratch']
    assert result['covscan'][0].headers['taskid'] == task_id
    assert result['covscan'][0].headers['item'] == '{} {}'.format(nvr, latest)

    assert result['covscan'][1].body['data']['item'] == '{} {}'.format(nvr, latest)
    assert result['covscan'][1].body['data']['newnvr'] == nvr
    assert result['covscan'][1].body['data']['oldnvr'] == latest
    assert result['covscan'][1].body['data']['scratch']
    assert result['covscan'][1].body['data']['taskid'] == task_id

    assert result['covscan'][1].body['outcome'] == overall_results
    assert result['covscan'][1].body['ref_url'] == covscan_url


def rpmdiff(result_type, module, monkeypatch):
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

    mocked_result = RpmdiffTestResult(module.ci, run_info, result_type, payload=[subresult, subresult, subresult])

    def mocked_shared(key):
        return {
            'task': mocked_task,
            'results': [mocked_result, mocked_result]
        }[key]

    monkeypatch.setattr(module, 'shared', mocked_shared)

    module.execute()

    result = module.bus_messages()
    result_type = 'rpmdiff-{}'.format(result_type)

    assert len(result) == 1
    assert len(result[result_type]) == 6

    assert result[result_type][0].headers['CI_TYPE'] == 'resultsdb'
    assert result[result_type][0].headers['type'] == rpmdiff_type
    assert result[result_type][0].headers['testcase'] == name
    assert result[result_type][0].headers['scratch']
    assert result[result_type][0].headers['taskid'] == task_id
    assert result[result_type][0].headers['item'] == item

    assert result[result_type][0].body == subresult


def test_rpmdiff_comparison(module, monkeypatch):
    rpmdiff('comparison', module, monkeypatch)


def test_rpmdiff_analysis(module, monkeypatch):
    rpmdiff('analysis', module, monkeypatch)


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

    module.execute()

    result = module.bus_messages()
    result_type = test_result.test_type

    assert len(result) == 1
    assert len(result[result_type]) == 2

    assert result[result_type][0].headers['CI_TYPE'] == 'ci-metricsdata'
    assert result[result_type][0].headers['component'] == nvr
    assert result[result_type][0].headers['taskid'] == task_id

    assert result[result_type][1].body['component'] == nvr
    assert result[result_type][1].body['trigger'] == 'brew build'

    assert result[result_type][1].body['tests'][0]['executor'] == 'CI_OSP' if result_type == 'restraint' else 'beaker'
    assert result[result_type][1].body['tests'][0]['executed'] == EXECUTED
    assert result[result_type][1].body['tests'][0]['failed'] == FAILED

    assert result[result_type][1].body['base_distro'] == distro
    assert result[result_type][1].body['task_id'] == task_id
    job_name = 'ci-{}-brew-{}-2-runtest'.format(component, target)
    assert result[result_type][1].body['job_name'] == job_name

    assert result[result_type][1].body['build_type'] == build_type
    assert result[result_type][1].body['jenkins_job_url'] == job_url
    assert result[result_type][1].body['jenkins_build_url'] == build_url
    assert result[result_type][1].body['build_number'] == 'unknown'
    assert result[result_type][1].body['CI_tier'] == 1
    assert result[result_type][1].body['team'] == 'baseos'
    # this need fix in other commit
    assert result[result_type][1].body['recipients'] == 'u,n,k,n,o,w,n'


def test_beaker(module, monkeypatch):
    ci, _ = module

    test_result = BeakerTestResult(ci, 'PASS', 'some_matrix', payload=DUMMY_PAYLOAD)
    functional_testing(test_result, module, monkeypatch)


def test_restraint(module, monkeypatch):
    ci, _ = module

    test_result = RestraintTestResult(ci, 'PASS', payload=DUMMY_PAYLOAD)
    functional_testing(test_result, module, monkeypatch)


def test_ci_metricsdata_no_brew(module, monkeypatch):
    _, module = module

    monkeypatch.setattr(module, 'store', MagicMock())

    assert_shared('primary_task', module.process_ci_metricsdata, 'dummy_result', 'dummy_result_type')
    module.store.assert_not_called()


def test_ci_metricsdata_no_distro(module, monkeypatch):
    _, module = module

    patch_shared(monkeypatch, module, {
        'primary_task': None,
    })

    monkeypatch.setattr(module, 'store', MagicMock())

    assert_shared('distro', module.process_ci_metricsdata, 'dummy_result', 'dummy_result_type')
    module.store.assert_not_called()
