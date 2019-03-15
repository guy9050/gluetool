import pytest
import gluetool
from mock import MagicMock, call
from gluetool_modules.helpers.install_mbs_build_execute import InstallMBSBuild
from gluetool_modules.libs.sut_installation_fail import SUTInstallationFailedError
from . import create_module, patch_shared

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

WORKAROUNDS_OUTPUT = """
workaround command
other workaround command
"""

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


@pytest.fixture(name='module')
def fixture_module():
    module = create_module(InstallMBSBuild)[1]
    return module


def test_loadable(module):
    ci = module.glue
    python_mod = ci._load_python_module('helpers/install_mbs_build_execute', 'pytest_install_mbs_build',
                                        'gluetool_modules/helpers/install_mbs_build_execute.py')

    assert hasattr(python_mod, 'InstallMBSBuild')


def test_guest_setup(module, monkeypatch):
    primary_task_mock = MagicMock()
    primary_task_mock.nsvc = NSVC
    guest_mock = MagicMock()
    execute_mock = MagicMock()
    guest_mock.execute = execute_mock
    run_mock = MagicMock()
    run_mock.stdout = ODCS_OUTPUT

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        MagicMock(return_value=run_mock)
    )

    patch_shared(monkeypatch, module, {
        'primary_task': primary_task_mock,
        'evaluate_instructions': MagicMock()
    })

    guests = [guest_mock, guest_mock]

    module.setup_guest(guests)

    calls = []

    for _ in guests:
        calls.append(call('curl -v {} --output /etc/yum.repos.d/mbs_build.repo'.format(REPO_URL)))
        calls.append(call('yum module reset -y {}'.format(NSVC)))
        calls.append(call('yum module enable -y {}'.format(NSVC)))
        calls.append(call('yum module install -y {}'.format(NSVC)))

    execute_mock.assert_has_calls(calls)
    assert execute_mock.call_count == len(calls)


def test_use_devel_module_and_profile(module, monkeypatch):
    module._config['use-devel-module'] = True
    module._config['profile'] = 'common'

    primary_task_mock = MagicMock()
    primary_task_mock.nsvc = NSVC
    primary_task_mock.name = NAME
    primary_task_mock.stream = STREAM
    primary_task_mock.version = VERSION
    primary_task_mock.context = CONTEXT
    guest_mock = MagicMock()
    execute_mock = MagicMock()
    guest_mock.execute = execute_mock
    run_mock = MagicMock()
    run_mock.stdout = ODCS_OUTPUT

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        MagicMock(return_value=run_mock)
    )

    patch_shared(monkeypatch, module, {
        'primary_task': primary_task_mock,
        'evaluate_instructions': MagicMock()
    })

    guests = [guest_mock, guest_mock]

    module.setup_guest(guests)

    calls = []

    for _ in guests:
        calls.append(call('curl -v {} --output /etc/yum.repos.d/mbs_build.repo'.format(REPO_URL)))
        calls.append(call('yum module reset -y {}'.format(NSVC_DEVEL_WITH_PROFILE)))
        calls.append(call('yum module enable -y {}'.format(NSVC_DEVEL_WITH_PROFILE)))
        calls.append(call('yum module install -y {}'.format(NSVC_DEVEL_WITH_PROFILE)))

    execute_mock.assert_has_calls(calls)
    assert execute_mock.call_count == len(calls)


def test_workarounds(module, monkeypatch):
    module._config['installation-workarounds'] = 'dummy_workaround_path'

    primary_task_mock = MagicMock()
    primary_task_mock.nsvc = NSVC
    guest_mock = MagicMock()
    execute_mock = MagicMock()
    guest_mock.execute = execute_mock
    run_mock = MagicMock()
    run_mock.stdout = ODCS_OUTPUT

    def evaluate_instructions_mock(workarounds, callbacks):
        callbacks['commands']('instructions', 'commands', workarounds.strip().split('\n'), 'context')

    monkeypatch.setattr(module.glue, 'shared_functions', {
        'evaluate_instructions': (None, evaluate_instructions_mock),
        'primary_task': (None, MagicMock(return_value=primary_task_mock))
    })

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        MagicMock(return_value=run_mock)
    )
    monkeypatch.setattr(
        'gluetool.utils.load_yaml',
        MagicMock(return_value=WORKAROUNDS_OUTPUT)
    )

    guests = [guest_mock, guest_mock]

    module.setup_guest(guests)

    calls = []

    for _ in guests:
        calls.append(call('workaround command'))
        calls.append(call('other workaround command'))
        calls.append(call('curl -v {} --output /etc/yum.repos.d/mbs_build.repo'.format(REPO_URL)))
        calls.append(call('yum module reset -y {}'.format(NSVC)))
        calls.append(call('yum module enable -y {}'.format(NSVC)))
        calls.append(call('yum module install -y {}'.format(NSVC)))

    execute_mock.assert_has_calls(calls)
    assert execute_mock.call_count == len(calls)


def test_odcs_fial(module, monkeypatch):

    run_mock = MagicMock()
    run_mock.stdout = ODCS_FAIL_OUTPUT

    guest_mock = MagicMock()
    guest_mock.guest.environment.arch = 'x86_64'

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        MagicMock(return_value=run_mock)
    )

    with pytest.raises(gluetool.GlueError, match=r"^Getting repo from ODCS failed$"):
        module._get_repo('dummy_nsvc', [guest_mock])


def test_odcs_command_fial(module, monkeypatch):

    run_mock = MagicMock()
    run_mock.side_effect = gluetool.glue.GlueCommandError('dummy_error', MagicMock(exit_code=1))

    guest_mock = MagicMock()
    guest_mock.guest.environment.arch = 'x86_64'

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        run_mock
    )

    with pytest.raises(gluetool.GlueError, match=r"^ODCS call failed$"):
        module._get_repo('dummy_nsvc', [guest_mock])


def test_execute_command_fail(module, monkeypatch):
    primary_task_mock = MagicMock()
    primary_task_mock.nsvc = NSVC
    guest_mock = MagicMock()
    execute_mock = MagicMock()
    execute_mock.side_effect = gluetool.glue.GlueCommandError('dummy_error', MagicMock(exit_code=1))
    guest_mock.execute = execute_mock
    run_mock = MagicMock()
    run_mock.stdout = ODCS_OUTPUT

    monkeypatch.setattr(
        'gluetool_modules.helpers.install_mbs_build_execute.Command.run',
        MagicMock(return_value=run_mock)
    )

    patch_shared(monkeypatch, module, {
        'primary_task': primary_task_mock,
        'evaluate_instructions': MagicMock()
    })

    guests = [guest_mock, guest_mock]

    with pytest.raises(SUTInstallationFailedError):
        module.setup_guest(guests)
