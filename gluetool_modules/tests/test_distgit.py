from collections import namedtuple

import pytest

from mock import MagicMock

import gluetool
import gluetool_modules.infrastructure.distgit
from . import assert_shared, create_module, patch_shared

DistGitRepository = namedtuple('DistGitRepository', ('url', 'branch', 'package'))


@pytest.fixture(name='module')
def fixture_module():
    return create_module(gluetool_modules.infrastructure.distgit.DistGit)[1]


def test_sanity_shared(module):
    assert module.glue.has_shared('dist_git_repository') is True


@pytest.mark.parametrize('method', ['artifact', 'force'])
def test_sanity_missing_required_options(module, method):
    # pylint: disable=protected-access
    module._config['method'] = method

    with pytest.raises(gluetool.utils.IncompatibleOptionsError,
                       match="missing required options for method '{}'".format(method)):
        module.sanity()


def test_missing_primary_task(module):
    assert_shared('primary_task', module.dist_git_repository)


def test_force(monkeypatch, module):
    # pylint: disable=protected-access
    module._config['method'] = 'force'

    mock_task = MagicMock(component='some-component')
    mock_other_task = MagicMock(component='other-component')

    patch_shared(monkeypatch, module, {
        'primary_task': mock_task,
        'eval_context': {}
    })

    # pylint: disable=protected-access
    module._config['branch'] = None
    with pytest.raises(gluetool.glue.GlueError,
                       match="Could not translate target to dist-git branch or branch is empty"):
        module.dist_git_repository()

    # pylint: disable=protected-access
    module._config['branch'] = 'some-branch'

    # pylint: disable=protected-access
    module._config['repository'] = None
    with pytest.raises(gluetool.glue.GlueError,
                       match="Could not translate target to dist-git repository or repository is empty"):
        module.dist_git_repository()

    # pylint: disable=protected-access
    module._config['repository'] = 'some-repo'

    assert module.dist_git_repository() == DistGitRepository('some-repo', 'some-branch', 'some-component')
    assert module.dist_git_repository(task=mock_other_task, branch='other-branch') == DistGitRepository(
        'some-repo', 'other-branch', 'other-component')


def test_artifact(monkeypatch, module):
    # pylint: disable=protected-access
    module._config['method'] = 'artifact'

    mock_task = MagicMock(component='some-component')
    patch_shared(monkeypatch, module, {
        'primary_task': mock_task,
        'eval_context': {}
    })

    pattern_map_mock = MagicMock(match=MagicMock)

    monkeypatch.setattr(gluetool_modules.infrastructure.distgit, 'PatternMap', pattern_map_mock)
    monkeypatch.setattr(gluetool_modules.infrastructure.distgit, 'render_template', lambda a: 'a-value')

    assert module.dist_git_repository() == DistGitRepository('a-value', 'a-value', 'some-component')
