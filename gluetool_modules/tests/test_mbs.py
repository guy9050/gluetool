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
    "modulemd": "---\ndocument: modulemd\nversion: 2\ndata:\n  name: rust-toolset\n  stream: rhel8\n  version: 820181105234334\n  context: b09eea91\n  summary: Rust\n  description: >-\n    Rust Toolset\n  license:\n    module:\n    - MIT\n  xmd:\n    mbs:\n      scmurl: git://pkgs.devel.redhat.com/modules/rust-toolset?#7981ffe74ef8badda5dfcc5407fb2d9a84af0d62\n      buildrequires:\n        platform:\n          stream: el8\n          filtered_rpms: []\n          version: 2\n          koji_tag: module-rhel-8.0.0-build\n          context: 00000000\n          ref: virtual\n        llvm-toolset:\n          stream: rhel8\n          filtered_rpms: []\n          version: 820181030213659\n          koji_tag: module-llvm-toolset-rhel8-820181030213659-9edba152\n          context: 9edba152\n          ref: 32f47423126c0c2cc8b3cb0d3711da2b6999c9aa\n        rust-toolset:\n          stream: rhel8\n          filtered_rpms: []\n          version: 820181105191008\n          koji_tag: module-rust-toolset-rhel8-820181105191008-b09eea91\n          context: b09eea91\n          ref: 14bbba9cd56090bb4cb350cebaeebd6804abdd6d\n      mse: TRUE\n      rpms:\n        cargo-vendor:\n          ref: bac28fbd3452f187aa2c154e604898c0cef32437\n        rust:\n          ref: 13df2ea8a6f55619da6c030e4452f0170fcd3530\n        rust-toolset:\n          ref: fc700a92b0484d05ccb70e7f0de0bc4891c48efd\n      commit: 7981ffe74ef8badda5dfcc5407fb2d9a84af0d62\n  dependencies:\n  - buildrequires:\n      llvm-toolset: [rhel8]\n      platform: [el8]\n      rust-toolset: [rhel8]\n    requires:\n      llvm-toolset: [rhel8]\n      platform: [el8]\n  profiles:\n    default:\n      rpms:\n      - rust-toolset\n  api:\n    rpms:\n    - cargo\n    - cargo-doc\n    - cargo-vendor\n    - rls-preview\n    - rust\n    - rust-analysis\n    - rust-doc\n    - rust-gdb\n    - rust-lldb\n    - rust-src\n    - rust-std-static\n    - rustfmt-preview\n  components:\n    rpms:\n      cargo-vendor:\n        rationale: Tool for bundling Rust dependencies\n        repository: git://pkgs.devel.redhat.com/rpms/cargo-vendor\n        cache: http://pkgs.devel.redhat.com/repo/pkgs/cargo-vendor\n        ref: stream-rhel-8\n        buildorder: 1\n        arches: [aarch64, i686, ppc64le, s390x, x86_64]\n      rust:\n        rationale: Rust compiler and tools\n        repository: git://pkgs.devel.redhat.com/rpms/rust\n        cache: http://pkgs.devel.redhat.com/repo/pkgs/rust\n        ref: stream-rhel-8\n        arches: [aarch64, i686, ppc64le, s390x, x86_64]\n      rust-toolset:\n        rationale: Meta package for rust-toolset.\n        repository: git://pkgs.devel.redhat.com/rpms/rust-toolset\n        cache: http://pkgs.devel.redhat.com/repo/pkgs/rust-toolset\n        ref: stream-rhel-8\n        arches: [aarch64, i686, ppc64le, s390x, x86_64]\n...\n",
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
    assert primary_task.task_arches == TaskArches(['aarch64', 'i686', 'ppc64le', 's390x', 'x86_64'])
