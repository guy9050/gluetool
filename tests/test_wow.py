# pylint: disable=protected-access
import re
import pytest

from mock import MagicMock
import libci
import libci.utils
from libci.modules.testing.wow import WorkflowTomorrow, NoTestAvailableError
from . import create_module, patch_shared, assert_shared


COMMON_SEQUENCIES = [
    ['bkr', 'workflow-tomorrow', '--dry', '--decision'],
    ['--taskparam', 'BASEOS_CI=true'],
    ['--taskparam', 'BEAKERLIB_RPM_DOWNLOAD_METHODS=yum\\ direct']
]
SHARED_DISTRO = ['distro', 'distro1']
SHARED_TASK = ['primary_task', MagicMock(component='c1')]
SHARED_PRODUCT = ['product', 'product1']


@pytest.fixture(name='module')
def fixture_module(monkeypatch):
    monkeypatch.setattr(libci.utils, "run_command", MagicMock())
    return create_module(WorkflowTomorrow)


@pytest.fixture(name='module_with_task')
def fixture_module_with_task(module, monkeypatch):
    ci, module = module

    patch_shared(monkeypatch, module, {
        'primary_task': MagicMock()
    })

    return ci, module


@pytest.fixture(name='module_with_shared', params=[
    [SHARED_TASK],
    [SHARED_TASK, SHARED_DISTRO],
    [SHARED_TASK, SHARED_PRODUCT],
    [SHARED_TASK, SHARED_DISTRO, SHARED_PRODUCT]
])
def fixture_module_with_shared(module, request, monkeypatch):
    ci, module = module

    if request.param:
        patch_shared(monkeypatch, module, {
            name: value for name, value in request.param
        })

    return ci, module


@pytest.fixture(name='configured_module')
def fixture_configured_module(module_with_task):
    ci, module = module_with_task
    module._config['wow-options'] = '--dummy-option dummy-value'
    return ci, module


def sublist_exists(sublist):
    # pylint: disable=no-member
    args, _ = libci.utils.run_command.call_args
    cmd = args[0]
    for i in range(len(cmd) - len(sublist) + 1):
        if sublist == cmd[i:i + len(sublist)]:
            return True
    return False


def test_shared(module):
    ci, _ = module
    assert ci.has_shared('beaker_job_xml') is True


def test_loadable(module):
    ci, _ = module
    # pylint: disable=protected-access
    python_mod = ci._load_python_module('testing/wow', 'pytest_wow',
                                        'libci/modules/testing/wow.py')

    assert hasattr(python_mod, 'WorkflowTomorrow')


@pytest.mark.parametrize("opts", [None, ''])
def test_sanity_fail(module, opts):
    _, module = module
    module._config['wow-options'] = opts
    with pytest.raises(NoTestAvailableError, match=r'No tests provided for the component'):
        module.sanity()


def test_sanity(configured_module):
    _, module = configured_module
    module.sanity()


@pytest.mark.parametrize('stderr', [
    'No relevant tasks found in test plan',
    'No recipe generated (no relevant tasks?)'
])
def test_common_command_failures(module_with_task, monkeypatch, stderr):
    _, module = module_with_task

    def faulty_run_command(cmd, **kwargs):
        # pylint: disable=unused-argument
        raise libci.CICommandError(cmd, MagicMock(exit_code=1, stderr=stderr))

    monkeypatch.setattr(libci.utils, 'run_command', faulty_run_command)
    with pytest.raises(NoTestAvailableError, match=r'No tests provided for the component'):
        module.beaker_job_xml()


def test_unrecognized_command_failure(module_with_task, monkeypatch):
    _, module = module_with_task
    monkeypatch.setattr(libci.utils, 'run_command', MagicMock(
        side_effect=libci.CICommandError([], MagicMock(exit_code=1, stderr='dummy error'))
    ))
    with pytest.raises(libci.CIError, match=r"Failure during 'wow' execution:"):
        module.beaker_job_xml()


def test_setup_phases_empty(module_with_task):
    _, module = module_with_task
    module.beaker_job_xml(setup_phases=[])
    assert not sublist_exists(['--setup'])


def test_include_module_wow_options(configured_module):
    _, module = configured_module
    module.beaker_job_xml()
    assert sublist_exists(['--dummy-option', 'dummy-value'])


def test_without_brew_task(module):
    _, module = module

    assert_shared('primary_task', module.beaker_job_xml)


@pytest.mark.parametrize('options,environment,task_params,setup_phases,expected_sequencies', [
    (
        None, None, None, None, COMMON_SEQUENCIES + [
            ['--setup', 'beakerlib'],
        ]),
    (
        ['--dummy-sw1', 'dummy-val1'], None, None, None, COMMON_SEQUENCIES + [
            ['--setup', 'beakerlib'],
            ['--dummy-sw1', 'dummy-val1']
        ]),
    (
        None, {'env1': 'v1'}, None, None, COMMON_SEQUENCIES),
    (
        None, None, {'p1': 'v1'}, None, COMMON_SEQUENCIES + [
            ['--setup', 'beakerlib'],
            ['--taskparam', 'p1=v1']
        ]),
    (
        None, None, {'BASEOS_CI': 'false', 'BEAKERLIB_RPM_DOWNLOAD_METHODS': 'false'}, None, [
            ['bkr', 'workflow-tomorrow', '--dry', '--decision'],
            ['--taskparam', 'BASEOS_CI=false'],
            ['--taskparam', 'BEAKERLIB_RPM_DOWNLOAD_METHODS=false'],
            ['--setup', 'beakerlib']
        ]),
    (
        None, None, {'p1': 'v1', 'p2': 'v2'}, None, COMMON_SEQUENCIES + [
            ['--setup', 'beakerlib'],
            ['--taskparam', 'p1=v1'],
            ['--taskparam', 'p2=v2']
        ]),
    (
        None, None, None, ['dummysetup1', 'dummysetup2'], COMMON_SEQUENCIES + [
            ['--setup', 'dummysetup1'],
            ['--setup', 'dummysetup2'],
        ]),
])
def test_with_basic_params(module_with_shared, options, environment, task_params, setup_phases, expected_sequencies):
    # pylint: disable=too-many-arguments,no-member
    ci, module = module_with_shared
    module.beaker_job_xml(options=options, environment=environment, task_params=task_params, setup_phases=setup_phases)
    args, _ = libci.utils.run_command.call_args
    cmd = args[0]

    expected_sequencies = expected_sequencies + [['--taskparam', 'BASEOS_CI_COMPONENT=c1']]
    if ci.has_shared('distro'):
        expected_sequencies = expected_sequencies + [['--distro', 'distro1']]
    if ci.has_shared('product'):
        env_data = cmd[cmd.index('--environment') + 1]
        assert re.match(r'.*product=product1.*', env_data)
    if environment:
        env_data = cmd[cmd.index('--environment') + 1]
        assert re.match(r'.*env1=v1.*', env_data)
    if ci.has_shared('product') and environment:
        assert sublist_exists(['--environment', 'product=product1 && env1=v1']) or \
            sublist_exists(['--environment', 'env1=v1 && product=product1'])

    for sequence in expected_sequencies:
        assert sublist_exists(sequence)