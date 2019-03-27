import pytest
import gluetool
import os
import shutil
from mock import MagicMock
from mock import call
from gluetool_modules.helpers.install_copr_build import InstallCoprBuild
from gluetool_modules.libs.sut_installation_fail import SUTInstallationFailedError
from . import create_module, patch_shared

LOG_DIR_NAME = 'dummy_log_dir_name'


def mock_guests(number, execute_mock):
    guests = []

    for i in range(number):
        guest_mock = MagicMock()
        guest_mock.name = 'guest{}'.format(i)
        guest_mock.execute = execute_mock
        guests.append(guest_mock)

    return guests


def assert_log_files(guests, file_names=None):
    if not file_names:
        file_names = [
            '0-Download-copr-repository.txt',
            '1-Reinstall-packages.txt',
            '2-Downgrade-packages.txt',
            '3-Update-packages.txt',
            '4-Install-packages.txt',
            '5-Verify-packages-installed.txt'
        ]

    for guest in guests:
        dir_name = '{}-{}'.format(LOG_DIR_NAME, guest.name)
        os.path.isdir(dir_name)
        for file_name in file_names:
            assert os.path.isfile(os.path.join(dir_name, file_name))


def cleanup_log_files(guests):
    for guest in guests:
        shutil.rmtree('{}-{}'.format(LOG_DIR_NAME, guest.name))


@pytest.fixture(name='module')
def fixture_module():
    module = create_module(InstallCoprBuild)[1]
    return module


@pytest.fixture(name='module_shared_patched')
def fixture_module_shared_patched(module, monkeypatch):
    primary_task_mock = MagicMock()
    primary_task_mock.repo_url = 'dummy_repo_url'
    primary_task_mock.rpm_urls = ['dummy_rpm_url1', 'dummy_rpm_url2']
    primary_task_mock.rpm_names = ['dummy_rpm_names1', 'dummy_rpm_names2']

    module._config['log-dir-name'] = 'dummy_log_dir_name'

    patch_shared(monkeypatch, module, {
        'primary_task': primary_task_mock,
        'setup_guest': None
    })

    return module, primary_task_mock


def test_loadable(module):
    ci = module.glue
    python_mod = ci._load_python_module('helpers/install_copr_build', 'pytest_install_copr_build',
                                        'gluetool_modules/helpers/install_copr_build.py')

    assert hasattr(python_mod, 'InstallCoprBuild')


def test_setup_guest(module_shared_patched):
    module, primary_task_mock = module_shared_patched

    execute_mock = MagicMock()
    guests = mock_guests(2, execute_mock)

    module.setup_guest(guests)

    calls = []

    for _ in guests:
        calls.append(call('curl -v dummy_repo_url --output /etc/yum.repos.d/copr_build.repo'))
        calls.append(call('yum -y reinstall dummy_rpm_url1'))
        calls.append(call('yum -y reinstall dummy_rpm_url2'))
        calls.append(call('yum -y downgrade dummy_rpm_url1 dummy_rpm_url2'))
        calls.append(call('yum -y update dummy_rpm_url1 dummy_rpm_url2'))
        calls.append(call('yum -y install dummy_rpm_url1 dummy_rpm_url2'))

    execute_mock.assert_has_calls(calls, any_order=True)
    assert_log_files(guests)
    cleanup_log_files(guests)


def test_no_yum(module_shared_patched):
    module, primary_task_mock = module_shared_patched

    def execute_mock_side_effect(cmd):
        if cmd == 'command -v yum':
            raise gluetool.glue.GlueCommandError('dummy_error', MagicMock(exit_code=1))
        return MagicMock()

    execute_mock = MagicMock()
    execute_mock.side_effect = execute_mock_side_effect

    guests = mock_guests(2, execute_mock)
    module.setup_guest(guests)

    calls = []

    for _ in guests:
        calls.append(call('curl -v dummy_repo_url --output /etc/yum.repos.d/copr_build.repo'))
        calls.append(call('dnf -y reinstall dummy_rpm_url1'))
        calls.append(call('dnf -y reinstall dummy_rpm_url2'))
        calls.append(call('dnf -y downgrade dummy_rpm_url1 dummy_rpm_url2'))
        calls.append(call('dnf -y update dummy_rpm_url1 dummy_rpm_url2'))
        calls.append(call('dnf -y install dummy_rpm_url1 dummy_rpm_url2'))

    execute_mock.assert_has_calls(calls, any_order=True)
    assert_log_files(guests)
    cleanup_log_files(guests)


def test_nvr_check_fails(module_shared_patched):
    module, primary_task_mock = module_shared_patched

    def execute_mock(cmd):
        if cmd.startswith('rpm -q') or cmd.startswith('yum -y downgrade'):
            raise gluetool.glue.GlueCommandError('dummy_error', MagicMock(exit_code=1))
        return MagicMock()

    guests = mock_guests(2, execute_mock)

    with pytest.raises(SUTInstallationFailedError):
        module.setup_guest(guests)

    assert_log_files(guests[:1])
    cleanup_log_files(guests[:1])


def test_repo_download_fails(module_shared_patched):
    module, primary_task_mock = module_shared_patched

    def execute_mock(cmd):
        if cmd.startswith('curl'):
            raise gluetool.glue.GlueCommandError('dummy_error', MagicMock(exit_code=1))
        return MagicMock()

    guests = mock_guests(2, execute_mock)

    with pytest.raises(SUTInstallationFailedError):
        module.setup_guest(guests)

    assert_log_files(guests[:1], file_names=['0-Download-copr-repository.txt'])
    cleanup_log_files(guests[:1])
