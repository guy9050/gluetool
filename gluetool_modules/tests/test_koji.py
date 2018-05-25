import logging
import os
import pytest
import koji
import functools

import gluetool
import gluetool_modules.infrastructure.koji_fedora

from mock import MagicMock
from . import create_module, testing_asset


class MockClientSession(object):
    """
    Mocked Koji session. It is given a source file which provides all necessary responses. The session
    reads the data, mocks its methods and replies accordingly to queries.
    """

    def __init__(self, source_file):
        data = gluetool.utils.load_yaml(source_file)

        assert data, 'Empty mock data provided in {}'.format(source_file)

        def getter(name, *args, **kwargs):
            assert name in data, "Attempt to use API endpoint '{}' which is not mocked".format(name)

            if args:
                assert args[0] in data[name], "Attempt to use API endpoint '{}({})' which is not mocked".format(
                    name, args[0])

                return data[name][args[0]]

            return data[name]

        for method, response in data.iteritems():
            setattr(self, method, functools.partial(getter, method))


@pytest.fixture(name='koji_session')
def fixture_koji_session(request, monkeypatch):
    # This is a bit complicated. We want parametrize this fixture, which is what indirect=True
    # does, but that somehow expecteds that all params are given to this fixture, while we want
    # thise give it just the task ID, and other params, e.g. NVR, are for the test itself.
    # To overcome that, request.params can be multiple packed params, this fixture will use
    # just the first one (task ID), return all of them, and test needs to unpack them as necessary.

    task_id = request.param[0] if isinstance(request.param, tuple) else request.param

    session = MockClientSession(testing_asset(os.path.join('koji', '{}.yml'.format(task_id))))

    monkeypatch.setattr(koji, 'ClientSession', MagicMock(return_value=session))

    return request.param


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

    #  make sure the module is loaded without a task specified
    mod.execute()

    return mod


def assert_task_attributes(module, task_id):
    """
    Assert helper. Given the task ID, it loads expected values of a task from the YAML file,
    and compares them to actual values of module's primary task.
    """

    primary_task = module.primary_task()

    expected_attributes = gluetool.utils.load_yaml(testing_asset('koji', 'task-{}.yml'.format(task_id)))

    for name, expected in expected_attributes.iteritems():
        actual = getattr(primary_task, name)
        assert actual == expected, "Field '{}' mismatch: {} expected, {} found".format(name, expected, actual)


@pytest.mark.parametrize('koji_session', [
    15869828,
    20166983,
    16311217
], indirect=True)
def test_task_by_id(koji_session, module):
    """
    Tasks are specified directly by their IDs.
    """

    module.tasks([koji_session])

    assert_task_attributes(module, koji_session)


@pytest.mark.parametrize('koji_session', [
    (15869828, True),
    (20166983, False),
    (16311217, True)
], indirect=True)
def test_task_by_task_id_option(koji_session, module):
    """
    Tasks are specified via module's ``--task-id`` option.
    """

    task_id, has_artifacts = koji_session

    # pylint: disable=protected-access
    module._config['task-id'] = [task_id]

    if has_artifacts:
        module.execute()

    else:
        with pytest.raises(gluetool_modules.infrastructure.koji_fedora.NoArtifactsError):
            module.execute()

    assert_task_attributes(module, task_id)


@pytest.mark.parametrize('koji_session', [
    (15869828, 'bash-4.3.43-4.fc25')
], indirect=True)
def test_task_by_nvr_option(koji_session, module):
    """
    Tasks are specified via module's ``--nvr`` option.
    """

    task_id, nvr = koji_session

    # pylint: disable=protected-access
    module._config['nvr'] = [nvr]

    module.execute()

    assert_task_attributes(module, task_id)


@pytest.mark.parametrize('koji_session', [
    (15869828, 805705)
], indirect=True)
def test_task_by_build_id_option(koji_session, module):
    """
    Tasks are specified via module's ``--build-id`` option.
    """

    task_id, build_id = koji_session

    # pylint: disable=protected-access
    module._config['build-id'] = [build_id]

    module.execute()

    assert_task_attributes(module, task_id)


@pytest.mark.parametrize('koji_session', [
    (15869828, 'bash', 'f25')
], indirect=True)
def test_task_by_name_and_tag_options(koji_session, module):
    """
    Tasks are specified via module's ``--name`` and ``--tag`` options.
    """

    task_id, name, tag = koji_session

    # pylint: disable=protected-access
    module._config.update({
        'name': name,
        'tag': tag
    })

    module.execute()

    assert_task_attributes(module, task_id)


def test_no_koji_task(module):
    """
    Module haven't been told to represent any tasks yet, however someone already asks for them.
    """

    with pytest.raises(gluetool.GlueError, match=r'No tasks specified\.'):
        module.tasks()


def test_invalid_task_id_type(module):
    """
    Invalid task ID passed to the module.
    """

    with pytest.raises(ValueError):
        module.tasks(['invalid id'])


@pytest.mark.parametrize('koji_session', [
    20171466
], indirect=True)
def test_not_valid_build_tasks(koji_session, module):
    """
    Tasks IDs represent tasks that are not valid build tasks.
    """

    module._config['valid-methods'] = ['build']

    with pytest.raises(gluetool.GlueError, match=r'Task is not a build task'):
        module.tasks([koji_session])


def test_missing_name_option(module):
    # pylint: disable=protected-access
    module._config['tag'] = 'f25'

    with pytest.raises(gluetool.GlueError, match=r"You need to specify package name with '--name' option"):
        module.sanity()


def test_missing_tag_option(module):
    # pylint: disable=protected-access
    module._config['name'] = 'bash'

    with pytest.raises(gluetool.GlueError, match=r"You need to specify 'tag' with package name"):
        module.sanity()


@pytest.mark.parametrize('koji_session', [
    705705
], indirect=True)
def test_invalid_build(koji_session, module, log):
    # pylint: disable=protected-access
    module._config['build-id'] = [koji_session]

    module.execute()

    log.match(levelno=logging.WARN, message='Looking for build 705705, remote server returned None - skipping this ID')
    assert module._tasks == []


@pytest.mark.parametrize('koji_session', [
    10166983
], indirect=True)
def test_request_missing(koji_session, module):
    with pytest.raises(gluetool.GlueError, match=r'Task 10166983 has no request field in task info'):
        module.tasks([koji_session])


@pytest.mark.parametrize('koji_session', [
    10166985
], indirect=True)
def test_request_length_invalid(koji_session, module):
    with pytest.raises(gluetool.GlueError, match=r'Task 10166985 has unexpected number of items in request field'):
        module.tasks([10166985])
