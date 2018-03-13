import os

import pytest

import gluetool
import gluetool_modules.helpers.ansible
import libci.guest

from mock import MagicMock

from . import create_module


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(gluetool_modules.helpers.ansible.Ansible)[1]


@pytest.fixture(name='local_guest')
def fixture_local_guest(module):
    return libci.guest.NetworkedGuest(module, '127.0.0.1', key=MagicMock())


def test_sanity(module):
    pass


def test_loadable(module):
    # pylint: disable=protected-access
    python_mod = module.glue._load_python_module('helpers/ansible', 'pytest_ansible',
                                                 'gluetool_modules/helpers/ansible.py')

    assert hasattr(python_mod, 'Ansible')


def test_shared(module):
    assert module.glue.has_shared('run_playbook')


def test_run_playbook(module, local_guest, monkeypatch):
    mock_output = MagicMock()
    mock_run_command = MagicMock(return_value=mock_output)

    monkeypatch.setattr(gluetool.utils, 'run_command', mock_run_command)

    output = module.run_playbook('dummy playbook file', [local_guest])

    assert output is mock_output

    mock_run_command.assert_called_once_with([
        'ansible-playbook', '-i', '127.0.0.1,', '--private-key', local_guest.key, os.path.abspath('dummy playbook file')
    ])


def test_error(log, module, local_guest, monkeypatch):
    # simulate output of failed ansible-playbook run, giving user JSON blob with an error message
    mock_error = gluetool.GlueCommandError([], output=MagicMock(stdout='fatal: {"msg": "dummy error message"}'))
    mock_run_command = MagicMock(side_effect=mock_error)

    monkeypatch.setattr(gluetool.utils, 'run_command', mock_run_command)

    with pytest.raises(gluetool.GlueError, message='Failure during Ansible playbook execution. See log for details.'):
        module.run_playbook('dummy playbook file', [local_guest])

    assert log.match(message='Ansible says: dummy error message')


def test_extra_vars(module, local_guest, monkeypatch):
    mock_run_command = MagicMock()

    monkeypatch.setattr(gluetool.utils, 'run_command', mock_run_command)

    module.run_playbook('dummy playbook file', [local_guest], variables={
        'FOO': 'bar'
    })

    mock_run_command.assert_called_once_with([
        'ansible-playbook', '-i', '127.0.0.1,', '--private-key', local_guest.key,
        '--extra-vars', 'FOO="bar"',
        os.path.abspath('dummy playbook file')
    ])


def test_dryrun(module, local_guest, monkeypatch):
    mock_run_command = MagicMock(return_value=None)

    monkeypatch.setattr(gluetool.utils, 'run_command', mock_run_command)
    monkeypatch.setattr(module.glue, '_dryrun_level', gluetool.glue.DryRunLevels.DRY)

    module.run_playbook('dummy playbook path', [local_guest])

    mock_run_command.assert_called_once_with([
        'ansible-playbook', '-i', '127.0.0.1,', '--private-key', local_guest.key,
        '-C',
        os.path.abspath('dummy playbook path')
    ])
