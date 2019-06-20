import pytest
import os
import shutil
import gluetool
from mock import MagicMock, call
from gluetool_modules.helpers.install_mbs_build_execute import InstallMBSBuild
from gluetool_modules.libs.sut_installation import SUTInstallationFailedError
from . import create_module, patch_shared, check_loadable

ODCS_OUTPUT = """
Waiting for command create on compose 72215 to finish.
{
    "arches": "x86_64",
    "builds": null,
    "flags": [],
    "id": 72215,
    "koji_event": null,
    "koji_task_id": null,
    "lookaside_repos": "",
    "modular_koji_tags": null,
    "module_defaults_url": null,
    "multilib_arches": "",
    "multilib_method": 0,
    "owner": "mkluson",
    "packages": null,
    "removed_by": null,
    "result_repo": "http://odcs.engineering.redhat.com/composes/latest-odcs-72215-1/compose/Temporary",
    "result_repofile": "http://odcs.engineering.redhat.com/composes/latest-odcs-72215-1/compose/Temporary/odcs-72215.repo",
    "results": [
        "repository"
    ],
    "sigkeys": "",
    "source": "httpd:2.4:820190206142837:9edba152",
    "source_type": 2,
    "state": 2,
    "state_name": "done",
    "state_reason": "Compose is generated successfully",
    "time_done": "2019-03-05T14:40:24Z",
    "time_removed": null,
    "time_submitted": "2019-03-05T14:40:09Z",
    "time_to_expire": "2019-03-06T14:40:09Z"
}
"""

ODCS_FAIL_OUTPUT = """
Waiting for command create on compose 72215 to finish.
{
    "arches": "x86_64",
    "builds": null,
    "flags": [],
    "id": 72215,
    "koji_event": null,
    "koji_task_id": null,
    "lookaside_repos": "",
    "modular_koji_tags": null,
    "module_defaults_url": null,
    "multilib_arches": "",
    "multilib_method": 0,
    "owner": "mkluson",
    "packages": null,
    "removed_by": null,
    "result_repo": "http://odcs.engineering.redhat.com/composes/latest-odcs-72215-1/compose/Temporary",
    "result_repofile": "http://odcs.engineering.redhat.com/composes/latest-odcs-72215-1/compose/Temporary/odcs-72215.repo",
    "results": [
        "repository"
    ],
    "sigkeys": "",
    "source": "httpd:2.4:820190206142837:9edba152",
    "source_type": 2,
    "state": 2,
    "state_name": "fail",
    "state_reason": "Compose is generated successfully",
    "time_done": "2019-03-05T14:40:24Z",
    "time_removed": null,
    "time_submitted": "2019-03-05T14:40:09Z",
    "time_to_expire": "2019-03-06T14:40:09Z"
}
"""

INFO_OUTPUT = """
Name             : mailman
Stream           : 2.1 [d][e]
Version          : 820181213140247
Context          : 77fc8825
Profiles         : common [d] [i]
Default profiles : common
Repo             : rhel-AppStream
Summary          : Electronic mail discussion and e-newsletter lists managing software
Description      : An initial version of the mailman mailing list management software
Artifacts        : mailman-3:2.1.29-4.module+el8+2450+4586b8cd.x86_64

Name             : mailman
Stream           : stream [d][e][a]
Version          : 820181213140247
Context          : 77fc8825
Profiles         : common [d] [i]
Default profiles : common
Repo             : odcs-100372
Summary          : Electronic mail discussion and e-newsletter lists managing software
Description      : An initial version of the mailman mailing list management software
Artifacts        : mailman-3:2.1.29-4.module+el8+2450+4586b8cd.x86_64

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled, [a]ctive]
"""

INFO_OUTPUT_NO_STREAM = """
Name             : mailman
Stream           : stream [d][a]
Version          : 820181213140247
Context          : 77fc8825
Profiles         : common [d] [i]
Default profiles : common
Repo             : odcs-100372
Summary          : Electronic mail discussion and e-newsletter lists managing software
Description      : An initial version of the mailman mailing list management software
Artifacts        : mailman-3:2.1.29-4.module+el8+2450+4586b8cd.x86_64

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled, [a]ctive]
"""

INFO_OUTPUT_NO_PROFILE_INSTALLED = """
Name             : mailman
Stream           : stream [d][e][a]
Version          : 820181213140247
Context          : 77fc8825
Profiles         : common [d]
Default profiles : common
Repo             : odcs-100372
Summary          : Electronic mail discussion and e-newsletter lists managing software
Description      : An initial version of the mailman mailing list management software
Artifacts        : mailman-3:2.1.29-4.module+el8+2450+4586b8cd.x86_64

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled, [a]ctive]
"""

INFO_OUTPUT_NO_PROFILES_AVAILABLE = """
Name             : mailman
Stream           : stream [d][e][a]
Version          : 820181213140247
Context          : 77fc8825
Default profiles : common
Repo             : odcs-100372
Summary          : Electronic mail discussion and e-newsletter lists managing software
Description      : An initial version of the mailman mailing list management software
Artifacts        : mailman-3:2.1.29-4.module+el8+2450+4586b8cd.x86_64

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled, [a]ctive]
"""

INFO_OUTPUT_NO_DEFAULT_PROFILE = """
Name             : mailman
Stream           : stream [d][e][a]
Version          : 820181213140247
Context          : 77fc8825
Profiles         : common [d]
Repo             : odcs-100372
Summary          : Electronic mail discussion and e-newsletter lists managing software
Description      : An initial version of the mailman mailing list management software
Artifacts        : mailman-3:2.1.29-4.module+el8+2450+4586b8cd.x86_64

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled, [a]ctive]
"""

INFO_OUTPUT_UNAVAILABLE_PROFILE = """
Name             : mailman
Stream           : stream [d][e][a]
Version          : 820181213140247
Context          : 77fc8825
Profiles         : common [d]
Default profiles : unavailable
Repo             : odcs-100372
Summary          : Electronic mail discussion and e-newsletter lists managing software
Description      : An initial version of the mailman mailing list management software
Artifacts        : mailman-3:2.1.29-4.module+el8+2450+4586b8cd.x86_64

Hint: [d]efault, [e]nabled, [x]disabled, [i]nstalled, [a]ctive]
"""

WORKAROUNDS_OUTPUT = [
    {'label': 'Apply workaround', 'command': 'workaround command'},
    {'label': 'Apply other workaround', 'command': 'other workaround command'}
]

REPO_URL = 'http://odcs.engineering.redhat.com/composes/latest-odcs-72215-1/compose/Temporary/odcs-72215.repo'

NAME = 'name'
STREAM = 'stream'
VERSION = 'version'
CONTEXT = 'context'
PROFILE = 'common'

NSVC = '{}:{}:{}:{}'.format(
    NAME,
    STREAM,
    VERSION,
    CONTEXT
)

NSVC_DEVEL_WITH_PROFILE = '{}-devel:{}:{}:{}/{}'.format(
    NAME,
    STREAM,
    VERSION,
    CONTEXT,
    PROFILE
)

LOG_DIR_NAME = 'artifact-installation'


def mock_guest(execute_mock):
    guest_mock = MagicMock()
    guest_mock.name = 'guest0'
    guest_mock.execute = execute_mock

    return guest_mock


def assert_log_files(guest, log_dirpath, file_names=None):
    if not file_names:
        file_names = [
            '0-Download-ODCS-repo.txt',
            '1-Verify-profile.txt',
            '2-Reset-module.txt',
            '3-Enable-module.txt',
            '4-Verify-module-enabled.txt',
            '5-Install-module.txt',
            '6-Verify-module-installed.txt'
        ]

    installation_log_dir = os.path.join(
        log_dirpath,
        '{}-{}'.format(LOG_DIR_NAME, guest.name)
    )

    os.path.isdir(installation_log_dir)

    for file_name in file_names:
        filepath = os.path.join(installation_log_dir, file_name)
        if not os.path.isfile(filepath):
            assert False, 'File {} should exist'.format(filepath)


@pytest.fixture(name='module')
def fixture_module():
    module = create_module(InstallMBSBuild)[1]

    module._config['log-dir-name'] = LOG_DIR_NAME

    return module


def test_loadable(module):
    check_loadable(module.glue, 'gluetool_modules/helpers/install_mbs_build_execute.py', 'InstallMBSBuild')


def test_guest_setup(module, monkeypatch, tmpdir):
    primary_task_mock = MagicMock()
    primary_task_mock.nsvc = NSVC
    primary_task_mock.stream = STREAM
    execute_mock = MagicMock(return_value=MagicMock(stdout=INFO_OUTPUT))
    run_mock = MagicMock(stdout=ODCS_OUTPUT)

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        MagicMock(return_value=run_mock)
    )

    patch_shared(monkeypatch, module, {
        'primary_task': primary_task_mock,
        'evaluate_instructions': MagicMock()
    })

    guest = mock_guest(execute_mock)
    module.setup_guest(guest, log_dirpath=str(tmpdir))

    calls = [
        call('curl -v {} --output /etc/yum.repos.d/mbs_build.repo'.format(REPO_URL)),
        call('yum module reset -y {}'.format(NSVC)),
        call('yum module enable -y {}'.format(NSVC)),
        call('yum module install -y {}'.format(NSVC))
    ]

    execute_mock.assert_has_calls(calls, any_order=True)
    assert_log_files(guest, str(tmpdir))


def test_use_devel_module_and_profile(module, monkeypatch, tmpdir):
    module._config['use-devel-module'] = True
    module._config['profile'] = 'common'

    primary_task_mock = MagicMock()
    primary_task_mock.nsvc = NSVC
    primary_task_mock.name = NAME
    primary_task_mock.stream = STREAM
    primary_task_mock.version = VERSION
    primary_task_mock.context = CONTEXT
    execute_mock = MagicMock(return_value=MagicMock(stdout=INFO_OUTPUT))
    run_mock = MagicMock(stdout=ODCS_OUTPUT)

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        MagicMock(return_value=run_mock)
    )

    patch_shared(monkeypatch, module, {
        'primary_task': primary_task_mock,
        'evaluate_instructions': MagicMock()
    })

    guest = mock_guest(execute_mock)
    module.setup_guest(guest, log_dirpath=str(tmpdir))

    calls = [
        call('curl -v {} --output /etc/yum.repos.d/mbs_build.repo'.format(REPO_URL)),
        call('yum module reset -y {}'.format(NSVC_DEVEL_WITH_PROFILE)),
        call('yum module enable -y {}'.format(NSVC_DEVEL_WITH_PROFILE)),
        call('yum module install -y {}'.format(NSVC_DEVEL_WITH_PROFILE))
    ]

    execute_mock.assert_has_calls(calls, any_order=True)
    assert_log_files(guest, str(tmpdir))


def test_workarounds(module, monkeypatch, tmpdir):
    module._config['installation-workarounds'] = 'dummy_workaround_path'

    primary_task_mock = MagicMock()
    primary_task_mock.nsvc = NSVC
    primary_task_mock.stream = STREAM
    execute_mock = MagicMock(return_value=MagicMock(stdout=INFO_OUTPUT))
    run_mock = MagicMock(stdout=ODCS_OUTPUT)

    def evaluate_instructions_mock(workarounds, callbacks):
        callbacks['steps']('instructions', 'commands', workarounds, 'context')

    patch_shared(monkeypatch, module, {}, callables={
        'evaluate_instructions': evaluate_instructions_mock,
        'primary_task': MagicMock(return_value=primary_task_mock)
    })

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        MagicMock(return_value=run_mock)
    )
    monkeypatch.setattr(
        'gluetool.utils.load_yaml',
        MagicMock(return_value=WORKAROUNDS_OUTPUT)
    )

    guest = mock_guest(execute_mock)
    module.setup_guest(guest, log_dirpath=str(tmpdir))

    calls = [
        call('workaround command'),
        call('other workaround command'),
        call('curl -v {} --output /etc/yum.repos.d/mbs_build.repo'.format(REPO_URL)),
        call('yum module reset -y {}'.format(NSVC)),
        call('yum module enable -y {}'.format(NSVC)),
        call('yum module install -y {}'.format(NSVC))
    ]

    execute_mock.assert_has_calls(calls, any_order=True)
    assert_log_files(guest, str(tmpdir), file_names=[
        '0-Apply-workaround.txt',
        '1-Apply-other-workaround.txt',
        '2-Download-ODCS-repo.txt',
        '3-Verify-profile.txt',
        '4-Reset-module.txt',
        '5-Enable-module.txt',
        '6-Verify-module-enabled.txt',
        '7-Install-module.txt',
        '8-Verify-module-installed.txt'
    ])


def test_odcs_fail(module, monkeypatch):

    run_mock = MagicMock()
    run_mock.stdout = ODCS_FAIL_OUTPUT

    guest_mock = MagicMock()
    guest_mock.guest.environment.arch = 'x86_64'

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        MagicMock(return_value=run_mock)
    )

    with pytest.raises(gluetool.GlueError, match=r"^Getting repo from ODCS failed$"):
        module._get_repo('dummy_nsvc', guest_mock)


def test_odcs_command_fail(module, monkeypatch):

    run_mock = MagicMock()
    run_mock.side_effect = gluetool.glue.GlueCommandError('dummy_error', MagicMock(exit_code=1))

    guest_mock = MagicMock()
    guest_mock.guest.environment.arch = 'x86_64'

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        run_mock
    )

    with pytest.raises(gluetool.GlueError, match=r"^ODCS call failed$"):
        module._get_repo('dummy_nsvc', guest_mock)


def test_execute_command_fail(module, monkeypatch, tmpdir):
    primary_task_mock = MagicMock()
    primary_task_mock.nsvc = NSVC
    execute_mock = MagicMock()
    execute_mock.side_effect = gluetool.glue.GlueCommandError('dummy_error', MagicMock(exit_code=1))
    run_mock = MagicMock()
    run_mock.stdout = ODCS_OUTPUT

    module._config['log-dir-name'] = LOG_DIR_NAME

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        MagicMock(return_value=run_mock)
    )

    patch_shared(monkeypatch, module, {
        'primary_task': primary_task_mock,
        'evaluate_instructions': MagicMock()
    })

    guest = mock_guest(execute_mock)

    with pytest.raises(SUTInstallationFailedError):
        module.setup_guest(guest, log_dirpath=str(tmpdir))


@pytest.mark.parametrize('info_output', [
    INFO_OUTPUT_NO_STREAM,
    INFO_OUTPUT_NO_PROFILE_INSTALLED,
    INFO_OUTPUT_NO_DEFAULT_PROFILE,
    INFO_OUTPUT_NO_PROFILES_AVAILABLE,
    INFO_OUTPUT_UNAVAILABLE_PROFILE,
    ''
])
def test_sut_installation_fail(module, monkeypatch, info_output, tmpdir):
    primary_task_mock = MagicMock()
    primary_task_mock.nsvc = NSVC
    primary_task_mock.stream = STREAM
    execute_mock = MagicMock(return_value=MagicMock(stdout=info_output))
    run_mock = MagicMock(stdout=ODCS_OUTPUT)

    module._config['log-dir-name'] = LOG_DIR_NAME

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        MagicMock(return_value=run_mock)
    )

    patch_shared(monkeypatch, module, {
        'primary_task': primary_task_mock,
        'evaluate_instructions': MagicMock()
    })

    guest = mock_guest(execute_mock)

    with pytest.raises(SUTInstallationFailedError):
        module.setup_guest(guest, log_dirpath=str(tmpdir))
