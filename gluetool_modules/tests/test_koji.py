import logging
import pytest
import koji

import gluetool
import gluetool_modules.infrastructure.koji_fedora

from . import create_module


# this dictionary provides minimal data needed fake koji's ClientSession for the tested koji tasks
FAKE_CLIENT_SESSION = {
    15869828: {
        'getBuildTarget': {
            'dest_tag_name': 'f25-updates-candidate',
        },
        'getUser': {
            'name': 'svashisht',
        },
        'getTaskInfo': {
            'method': 'build',
            'owner': 3543,
            'request': [
                'git://pkgs.fedoraproject.org/rpms/bash?#b1104ec130056866f3bdce51a3f77685b702fbde',
                'f25-candidate',
                {}
            ],
            'state': 2,
        },
        'listBuilds': [{
            'build_id': 805705,
            'nvr': 'bash-4.3.43-4.fc25',
            'package_name': 'bash',
            'release': '4.fc25',
            'version': '4.3.43',
        }],
        'listTagged': [
            {'nvr': 'bash-4.3.43-4.fc25'},
            {'nvr': 'bash-4.3.43-3.fc25'}
        ],
    },
    18214941: {
        'getTaskInfo': {
            'method': 'build',
            'owner': 3823,
            'request': [
                'git://pkgs.fedoraproject.org/rpms/setup?#338c72bc4e4fc6fa950f899bf4253b32de1dff60',
                'rawhide',
                {'scratch': True}
            ],
            'state': 2
        },
        'listTasks': [
            {'id': 18214958},
        ],
        'listTaskOutput': {18214958: []}
    },
    20166983: {
        'getBuildTarget': {
            'dest_tag_name': 'f27-pending',
        },
        'getUser': {
            'name': 'mvadkert',
        },
        'getTaskInfo': {
            'method': 'build',
            'owner': 3823,
            'request': [
                'cli-build/1498396792.492652.jYJCrkUF/bash-4.4.12-5.fc26.src.rpm',
                None,
                {'scratch': True}
            ],
            'state': 2
        },
        'listTagged': [
            {'nvr': 'bash-4.4.12-5.fc27'},
            {'nvr': 'bash-4.4.12-4.fc27'}
        ],
        'listTasks': [
            {'id': 20166985},
        ],
        'listTaskOutput': {
            20166985: [
                'bash-4.4.12-5.fc27.src.rpm'
            ]
        }
    },
    20171466: {
        'getTaskInfo': {
            'method': 'runroot',
            'state': 2
        },
    },
    'bash-4.3.43-4.fc25': {
        'getBuildTarget': {
            'dest_tag_name': 'f25-updates-candidate',
        },
        'getBuild': {
            'task_id': 15869828
        },
        'getTaskInfo': {
            'method': 'build',
            'owner': 3543,
            'request': [
                'git://pkgs.fedoraproject.org/rpms/bash?#b1104ec130056866f3bdce51a3f77685b702fbde',
                'f25-candidate',
                {}
            ],
            'state': 2,
        },
        'listBuilds': [{
            'build_id': 805705,
            'nvr': 'bash-4.3.43-4.fc25',
            'package_name': 'bash',
            'release': '4.fc25',
            'version': '4.3.43',
        }],
    },
    '805705': {
        'getBuildTarget': {
            'dest_tag_name': 'f25-updates-candidate',
        },
        'getBuild': {
            'task_id': 15869828
        },
        'getTaskInfo': {
            'method': 'build',
            'owner': 3543,
            'request': [
                'git://pkgs.fedoraproject.org/rpms/bash?#b1104ec130056866f3bdce51a3f77685b702fbde',
                'f25-candidate',
                {}
            ],
            'state': 2,
        },
        'listBuilds': [{
            'build_id': 805705,
            'nvr': 'bash-4.3.43-4.fc25',
            'package_name': 'bash',
            'release': '4.fc25',
            'version': '4.3.43',
        }],
    },
    'bash': {
        'getBuildTarget': {
            'dest_tag_name': 'f25-updates-candidate',
        },
        'getTaskInfo': {
            'method': 'build',
            'owner': 3543,
            'request': [
                'git://pkgs.fedoraproject.org/rpms/bash?#b1104ec130056866f3bdce51a3f77685b702fbde',
                'f25-candidate',
                {}
            ],
            'state': 2,
        },
        'listBuilds': [{
            'build_id': 805705,
            'nvr': 'bash-4.3.43-4.fc25',
            'package_name': 'bash',
            'release': '4.fc25',
            'version': '4.3.43',
        }],
        'listTagged': [{
            'task_id': 15869828
        }],
    },
    # invalid build
    705705: {
        'getBuild': [None]
    }
}


# Dictionary of valid tasks and their expected properties
# Make sure task id can be int or a string
VALID_TASKS = {
    # https://koji.fedoraproject.org/koji/taskinfo?taskID=15869828
    15869828: {
        'build_id': 805705,
        'component': 'bash',
        'destination_tag': 'f25-updates-candidate',
        'full_name': "task '15869828' build 'bash-4.3.43-4.fc25' target 'f25-candidate'",
        'issuer': 'svashisht',
        'owner': 'svashisht',
        'latest': 'bash-4.3.43-3.fc25',
        'nvr': 'bash-4.3.43-4.fc25',
        'pkgs_url': 'https://kojipkgs.fedoraproject.org',
        'release': '4.fc25',
        'scratch': False,
        'short_name': '15869828:bash-4.3.43-4.fc25',
        'srcrpm': 'https://kojipkgs.fedoraproject.org/packages/bash/4.3.43/4.fc25/src/bash-4.3.43-4.fc25.src.rpm',
        'target': 'f25-candidate',
        'task_id': 15869828,
        'url': 'https://koji.fedoraproject.org/koji/taskinfo?taskID=15869828',
        'version': '4.3.43',
    },
    20166983: {
        'build_id': None,
        'component': 'bash',
        'destination_tag': 'f27-pending',
        'full_name': "task '20166983' scratch build 'bash-4.4.12-5.fc27' target '<no build target available>'",
        'issuer': 'mvadkert',
        'owner': 'mvadkert',
        'latest': 'bash-4.4.12-5.fc27',
        'nvr': 'bash-4.4.12-5.fc27',
        'pkgs_url': 'https://kojipkgs.fedoraproject.org',
        'release': '5.fc27',
        'scratch': True,
        'short_name': '20166983:S:bash-4.4.12-5.fc27',
        'srcrpm': 'https://kojipkgs.fedoraproject.org/work/tasks/6985/20166985/bash-4.4.12-5.fc27.src.rpm',
        'target': '<no build target available>',
        'task_id': 20166983,
        'url': 'https://koji.fedoraproject.org/koji/taskinfo?taskID=20166983',
        'version': '4.4.12',
    }
}

# invalid non-build tasks
# runroot, newRepo, appliance, livemedia, image
NON_BUILD_TASKS = [20171466]

# scratch builds which have artifacts already gone
EXPIRED_TASKS = [18214941]


class FakeClientSession(object):
    def __init__(self, *args, **kwargs):
        pass

    # pylint: disable=invalid-name,unused-argument
    @staticmethod
    def getAPIVersion():
        return '1'

    # pylint: disable=invalid-name,unused-argument
    def getBuildTarget(self, *args, **kwargs):
        return FAKE_CLIENT_SESSION[self.fake_key]['getBuildTarget']

    # pylint: disable=invalid-name,unused-argument
    def getBuild(self, *args, **kwargs):
        return FAKE_CLIENT_SESSION[self.fake_key]['getBuild']

    # pylint: disable=invalid-name,unused-argument
    def getUser(self, *args, **kwargs):
        return FAKE_CLIENT_SESSION[self.fake_key]['getUser']

    # pylint: disable=invalid-name,unused-argument
    def getTaskInfo(self, *args, **kwargs):
        return FAKE_CLIENT_SESSION[self.fake_key]['getTaskInfo']

    # pylint: disable=invalid-name,unused-argument
    def listBuilds(self, *args, **kwargs):
        return FAKE_CLIENT_SESSION[self.fake_key]['listBuilds']

    # pylint: disable=invalid-name,unused-argument
    def listTagged(self, *args, **kwargs):
        return FAKE_CLIENT_SESSION[self.fake_key]['listTagged']

    # pylint: disable=invalid-name,unused-argument
    def listTasks(self, *args, **kwargs):
        return FAKE_CLIENT_SESSION[self.fake_key]['listTasks']

    # pylint: disable=invalid-name,unused-argument
    def listTaskOutput(self, tid):
        return FAKE_CLIENT_SESSION[self.fake_key]['listTaskOutput'][tid]


@pytest.fixture(name='module')
def fixture_module(monkeypatch):
    # pylint: disable=unused-argument
    ci, mod = create_module(gluetool_modules.infrastructure.koji_fedora.Koji)

    # make sure task has required share function
    assert ci.has_shared('tasks')
    assert ci.has_shared('primary_task')

    # pylint: disable=protected-access
    mod._config = {
        'url': 'https://koji.fedoraproject.org/kojihub',
        'pkgs-url': 'https://kojipkgs.fedoraproject.org',
        'web-url': 'https://koji.fedoraproject.org/koji',
    }

    # monkeypatch koji to use FakeClientSession instead of ClientSession
    monkeypatch.setattr(koji, 'ClientSession', FakeClientSession)

    #  make sure the module is loaded without a task specified
    mod.execute()

    # fake a key, used by most of the modules
    FakeClientSession.fake_key = 15869828

    return mod


def test_sanity_task_id(module):
    # sanity tests for various types of valid tasks specified by task_id
    for task in VALID_TASKS.iterkeys():
        FakeClientSession.fake_key = task
        module.tasks([task])
        for prop, val in VALID_TASKS[task].iteritems():
            # pylint: disable=protected-access
            assert getattr(module._tasks[0], prop) == val


def test_sanity_task_id_cmdline(module):
    # valid task specified by nvr
    FakeClientSession.fake_key = 15869828
    # pylint: disable=protected-access
    module._config.update({'task-id': [15869828]})
    module.execute()
    for prop, val in VALID_TASKS[15869828].iteritems():
        assert getattr(module._tasks[0], prop) == val


def test_sanity_nvr(module):
    # valid task specified by nvr
    FakeClientSession.fake_key = 'bash-4.3.43-4.fc25'
    # pylint: disable=protected-access
    module._config = {'nvr': ['bash-4.3.43-4.fc25']}
    module.execute()
    assert module._tasks[0].task_id == 15869828


def test_sanity_build_id(module):
    # valid task specified by build ID
    FakeClientSession.fake_key = '805705'
    # pylint: disable=protected-access
    module._config = {'build-id': [805705]}
    module.execute()
    assert module._tasks[0].task_id == 15869828


def test_sanity_name_tag(module):
    # valid task specified by package name and tag
    FakeClientSession.fake_key = 'bash'
    # pylint: disable=protected-access
    module._config = {
        'name': 'bash',
        'tag': 'f25'
    }
    module.execute()
    assert module._tasks[0].task_id == 15869828


def test_no_koji_task(module):
    # no koji task specified
    with pytest.raises(gluetool.GlueError, message='no koji task ID specified'):
        module.tasks()


def test_invalid_task_id_type(module):
    # task_id not convertable to a number
    with pytest.raises(ValueError):
        module.tasks(['invalid id'])


def test_not_valid_build_tasks(module):
    # not finished build tasks
    for task in NON_BUILD_TASKS:
        FakeClientSession.fake_key = task

        with pytest.raises(gluetool.GlueError, match=r'Task is not a valid, finished build task'):
            module.tasks([task])


def test_unavailable_artifacts(module):
    # not finished build tasks
    for task in EXPIRED_TASKS:
        FakeClientSession.fake_key = task
        with pytest.raises(gluetool.GlueError, match=r'No artifacts found for task'):
            module.tasks([task])


def test_missing_name(module):
    # pylint: disable=protected-access
    module._config = {
        'tag': 'f25',
    }

    match = "You need to specify package name with '--name' option"
    with pytest.raises(gluetool.GlueError, match=match):
        module.sanity()


def test_missing_tag(module):
    # pylint: disable=protected-access
    module._config = {
        'name': ['bash'],
    }

    match = "You need to specify 'tag' with package name"
    with pytest.raises(gluetool.GlueError, match=match):
        module.sanity()


def test_invalid_build(log, module):
    # pylint: disable=protected-access
    FakeClientSession.fake_key = 705705
    module._config = {
        'build-id': [705705]
    }

    module.execute()

    log.match(levelno=logging.WARN, message='Looking for build 705705, remote server returned None - skipping this ID')
    assert module._tasks == []
