import pytest

import gluetool
import gluetool_modules.infrastructure.mbs
from gluetool_modules.infrastructure.mbs import MBS, TaskArches
from . import create_module

from mock import MagicMock

TARGET = 'module-rhel8'
ARCH = 'x86_64'

MBS_INFO = {
    "component_builds": [
        38974,
        38972,
        38971,
        38973
    ],
    "context": "b09eea91",
    "id": 2178,
    "koji_tag": "module-rust-toolset-rhel8-820181105234334-b09eea91",
    "name": "rust-toolset",
    "owner": "jistone",
    "rebuild_strategy": "only-changed",
    "scmurl": "git://pkgs.devel.redhat.com/modules/rust-toolset?#7981ffe74ef8badda5dfcc5407fb2d9a84af0d62",
    "siblings": [],
    "state": 5,
    "state_name": "ready",
    "state_reason": 'null',
    "stream": "rhel8",
    "tasks": {
        "rpms": {
            "cargo-vendor": {
                "nvr": "cargo-vendor-0.1.15-3.module+el8+2176+447ca617",
                "state": 1,
                "state_reason": "Reused component from previous module build",
                "task_id": 19068986
            },
            "module-build-macros": {
                "nvr": "module-build-macros-0.1-1.module+el8+2178+cc252a44",
                "state": 1,
                "state_reason": "",
                "task_id": 19069801
            },
            "rust": {
                "nvr": "rust-1.28.0-1.module+el8+2178+cc252a44",
                "state": 1,
                "state_reason": "",
                "task_id": 19069916
            },
            "rust-toolset": {
                "nvr": "rust-toolset-1.28.0-1.module+el8+2178+cc252a44",
                "state": 1,
                "state_reason": "",
                "task_id": 19069917
            }
        }
    },
    "time_completed": "2018-11-06T03:25:28Z",
    "time_modified": "2018-11-06T03:25:57Z",
    "time_submitted": "2018-11-05T23:43:59Z",
    "version": "820181105234334"
}


@pytest.fixture(name='module')
def fixture_module():
    return create_module(MBS)[1]


def test_loadable(module):
    ci = module.glue
    python_mod = ci._load_python_module('infrastructure/mbs', 'pytest_mbs',
                                        'gluetool_modules/infrastructure/mbs.py')

    assert hasattr(python_mod, 'MBS')


def test_execute(module, monkeypatch):
    module._config['task-id'] = '2178'
    module._config['target'] = TARGET
    module._config['arches'] = ARCH

    dummy_request = MagicMock()
    dummy_request.json.return_value = MBS_INFO

    monkeypatch.setattr(gluetool_modules.infrastructure.mbs.requests, 'get', MagicMock(return_value=dummy_request))

    assert module.eval_context == {}

    module.execute()

    eval_context = module.eval_context
    primary_task = module.primary_task()

    assert eval_context['ARTIFACT_TYPE'] == 'redhat-module'
    assert eval_context['BUILD_TARGET'] == primary_task.target
    assert eval_context['PRIMARY_TASK'] == primary_task
    assert eval_context['TASKS'] == module.tasks()

    assert primary_task.name == 'rust-toolset'
    assert primary_task.component == 'rust-toolset'
    assert primary_task.stream == 'rhel8'
    assert primary_task.version == '820181105234334'
    assert primary_task.context == 'b09eea91'
    assert primary_task.issuer == 'jistone'
    assert primary_task.nsvc == 'rust-toolset:rhel8:820181105234334:b09eea91'
    assert primary_task.nvr == 'rust-toolset:rhel8:820181105234334:b09eea91'
    assert primary_task.component_id == 'rust-toolset:rhel8'

    assert primary_task.target == TARGET
    assert primary_task.task_arches == TaskArches([ARCH])
