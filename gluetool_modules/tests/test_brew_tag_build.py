import pytest

from mock import MagicMock

import gluetool.utils
from gluetool_modules.helpers import brew_tag_build
from . import create_module, patch_shared, assert_shared, check_loadable


@pytest.fixture(name='module')
def fixture_module():
    module = create_module(brew_tag_build.CIBrewTagBuild)[1]

    module._config['tag-group'] = 'dummy-group'
    module._config['config'] = 'dummy-config'

    return module


def _patch_module(module, monkeypatch, mocked_result=None, scratch=False):
    mock_primary_task = MagicMock()
    mock_primary_task.scratch = scratch
    mock_primary_task.target = 'dummy-target'
    mock_primary_task.nvr = 'dummy-nvr'

    patch_shared(monkeypatch, module, {
        'primary_task': mock_primary_task,
        'results': [mocked_result] if mocked_result else []
    })

    output = MagicMock()
    output.stdout = 'dummy-stdout '
    output.stderr = 'dummy-stderr'
    output.exit_code = 1
    mock_run_command = MagicMock()
    mock_run_command.side_effect = gluetool.GlueCommandError('dummy-command', output)
    monkeypatch.setattr(brew_tag_build, 'run_command', mock_run_command)

    return module, mock_run_command


def test_loadable(module):
    check_loadable(module.glue, 'gluetool_modules/helpers/brew_tag_build.py', 'CIBrewTagBuild')


def test_execute_no_primary_task(module):
    assert_shared('primary_task', module.execute)


def test_execute_scratch_build(module, monkeypatch, log):
    module, mock_run_command = _patch_module(module, monkeypatch, scratch=True)
    module.execute()
    assert 'cowardly refusing to tag scratch build' in log.records[-1].message
    assert not mock_run_command.called


def test_execute_no_results(module, monkeypatch, log):
    module, mock_run_command = _patch_module(module, monkeypatch, scratch=False)

    module.execute()
    assert 'no results found, skipping' in log.records[-1].message
    assert not mock_run_command.called


@pytest.mark.parametrize('test_type, overall_result', [
    ('beaker', 'FAIL'),
    ('restraint', 'FAIL'),
    ('beaker', 'ERROR'),
    ('restraint', 'ERROR')
])
def test_execute_failed_results(module, monkeypatch, log, test_type, overall_result):
    mocked_result = MagicMock()
    mocked_result.test_type = test_type
    mocked_result.overall_result = overall_result

    module, mock_run_command = _patch_module(module, monkeypatch, mocked_result=mocked_result, scratch=False)

    module.execute()
    assert 'some tests failed, cannot apply the tag' in log.records[-1].message
    assert not mock_run_command.called


def test_execute_tag_keyerror(module, monkeypatch):
    mocked_result = MagicMock()
    mocked_result.test_type = 'beaker'
    mocked_result.overall_result = 'PASS'

    module, _ = _patch_module(module, monkeypatch, mocked_result=mocked_result, scratch=False)

    monkeypatch.setattr(brew_tag_build, 'load_yaml',
                        MagicMock(return_value={'dummy': 'dummy'}))

    with pytest.raises(gluetool.GlueError, match="unknown tag group 'dummy-group'"):
        module.execute()


def test_execute_no_tags_to_apply(module, monkeypatch, log):
    mocked_result = MagicMock()
    mocked_result.test_type = 'beaker'
    mocked_result.overall_result = 'PASS'

    module, mock_run_command = _patch_module(module, monkeypatch, mocked_result=mocked_result, scratch=False)

    monkeypatch.setattr(brew_tag_build, 'load_yaml',
                        MagicMock(return_value={'dummy-group': [{'dummy': 'dummy'}]}))

    module.execute()

    assert "no tags to apply for build target 'dummy-target'" in log.records[-1].message
    assert not mock_run_command.called


def test_execute_already_tagged(module, monkeypatch, log):
    mocked_result = MagicMock()
    mocked_result.test_type = 'beaker'
    mocked_result.overall_result = 'PASS'

    module, _ = _patch_module(module, monkeypatch, mocked_result=mocked_result, scratch=False)

    monkeypatch.setattr(brew_tag_build, 'load_yaml',
                        MagicMock(return_value={'dummy-group': [{'dummy-target': 'dummy-target-value'}]}))

    output = MagicMock()
    output.stdout = 'already tagged'
    output.stderr = 'dummy-stderr'
    output.exit_code = 1

    mock_run_command = MagicMock()
    mock_run_command.side_effect = gluetool.GlueCommandError('dummy-command', output)
    monkeypatch.setattr(brew_tag_build, 'run_command', mock_run_command)

    module.execute()

    assert 'build already tagged, cowardly skipping' in log.records[-1].message

    command = ['brew', 'tag-build', 'dummy-target-value', 'dummy-nvr']
    assert mock_run_command.called_with(command)


def test_execute_run_command_error(module, monkeypatch):
    mocked_result = MagicMock()
    mocked_result.test_type = 'beaker'
    mocked_result.overall_result = 'PASS'

    module, mock_run_command = _patch_module(module, monkeypatch, mocked_result=mocked_result, scratch=False)

    monkeypatch.setattr(brew_tag_build, 'load_yaml',
                        MagicMock(return_value={'dummy-group': [{'dummy-target': 'dummy-target-value'}]}))

    with pytest.raises(gluetool.GlueError, match="Failure during 'brew' execution: dummy-stdout dummy-stderr"):
        module.execute()

    command = ['brew', 'tag-build', 'dummy-target-value', 'dummy-nvr']
    assert mock_run_command.called_with(command)


def test_sanity(module, monkeypatch):
    mock_check_for_command = MagicMock()
    monkeypatch.setattr(brew_tag_build, 'check_for_commands', mock_check_for_command)

    module.sanity()
    assert mock_check_for_command.called_with(brew_tag_build.REQUIRED_CMDS)
