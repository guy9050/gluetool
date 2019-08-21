import collections
import logging

import pytest

from mock import MagicMock

import gluetool
import gluetool_modules.infrastructure.distgit
from gluetool_modules.infrastructure.distgit import DistGitRepository
from . import assert_shared, create_module, patch_shared

Response = collections.namedtuple('Response', ['status_code', 'content'])


@pytest.fixture(name='module')
def fixture_module():
    return create_module(gluetool_modules.infrastructure.distgit.DistGit)[1]


@pytest.fixture(name='dummy_repository')
def fixture_dummy_repository(module):
    return DistGitRepository(module, 'some-package', clone_url='some-clone-url', web_url='some-web-url', branch='some-branch')


def test_sanity_shared(module):
    assert module.glue.has_shared('dist_git_repository') is True


@pytest.mark.parametrize('method', ['artifact', 'force'])
def test_sanity_missing_required_options(module, method):
    module._config['method'] = method

    with pytest.raises(gluetool.utils.IncompatibleOptionsError,
                       match="missing required options for method '{}'".format(method)):
        module.sanity()


def test_missing_primary_task(module):
    assert_shared('primary_task', module.execute)


def test_force(monkeypatch, module):
    module._config['method'] = 'force'

    mock_task = MagicMock(component='some-component')
    mock_other_task = MagicMock(component='other-component')

    patch_shared(monkeypatch, module, {
        'primary_task': mock_task,
        'eval_context': {}
    })

    with pytest.raises(gluetool.glue.GlueError,
                       match="Could not acquire dist-git clone URL"):
        module.execute()

    module._config['clone-url'] = 'some-clone-url'

    with pytest.raises(gluetool.glue.GlueError,
                       match="Could not acquire dist-git web URL"):
        module.execute()

    module._config['web-url'] = 'some-web-url'

    module._config['branch'] = 'some-branch'
    module._config['ref'] = 'some-ref'

    module.execute()
    repository = module.dist_git_repository()

    assert repository.package == 'some-component'
    assert repository.clone_url == 'some-clone-url'
    assert repository.web_url == 'some-web-url'
    assert repository.branch == 'some-branch'
    assert repository.ref == 'some-ref'


def test_artifact(monkeypatch, module):
    module._config['method'] = 'artifact'

    mock_task = MagicMock(component='some-component')
    patch_shared(monkeypatch, module, {
        'primary_task': mock_task,
        'eval_context': {}
    })

    pattern_map_mock = MagicMock(match=MagicMock)

    monkeypatch.setattr(gluetool_modules.infrastructure.distgit, 'PatternMap', pattern_map_mock)
    monkeypatch.setattr(gluetool_modules.infrastructure.distgit, 'render_template', lambda a: 'a-value')

    module.execute()
    repository = module.dist_git_repository()

    assert repository.package == 'some-component'
    assert repository.clone_url == 'a-value'
    assert repository.web_url == 'a-value'
    assert repository.branch == 'a-value'


def test_eval_context(module, dummy_repository, monkeypatch):
    monkeypatch.setattr(module, '_repository', dummy_repository)

    assert module.eval_context['DIST_GIT_REPOSITORY'] is dummy_repository


def test_eval_context_recursion(module, monkeypatch):
    monkeypatch.setattr(gluetool_modules.libs, 'is_recursion', MagicMock(return_value=True))

    assert module.eval_context == {}


def test_repr(module, dummy_repository):
    assert dummy_repository.__repr__() == '<DistGitRepository(package="some-package", branch="some-branch")>'


class MockRequests(object):
    status_code = 200
    response = '# recipients: batman, robin\ndata'

    def __enter__(self, *args):
        return self

    def __exit__(self, *args):
        pass

    @staticmethod
    def get(_):
        return Response(MockRequests.status_code, MockRequests.response)


def test_gating(module, dummy_repository, monkeypatch, log):
    # gating configuration found
    monkeypatch.setattr(gluetool.utils, 'requests', MockRequests)

    assert dummy_repository.has_gating
    assert log.match(message=(
        "gating configuration 'some-web-url/raw/some-branch/f/gating.yaml':\n"
        "---v---v---v---v---v---\n"
        "# recipients: batman, robin\n"
        "data\n"
        "---^---^---^---^---^---"
    ))


def test_no_gating(module, dummy_repository, monkeypatch, log):
    # gating configuration not found
    monkeypatch.setattr(gluetool.utils, 'requests', MockRequests)
    monkeypatch.setattr(MockRequests, 'status_code', 400)

    assert dummy_repository.has_gating is False
    assert log.match(message="dist-git repository has no gating.yaml 'some-web-url/raw/some-branch/f/gating.yaml'")


def test_repository_persistance(module, dummy_repository):
    module._repository = dummy_repository

    assert module.dist_git_repository() is dummy_repository


def test_gating_recipients(module, dummy_repository, monkeypatch):
    # gating configuration found
    monkeypatch.setattr(gluetool.utils, 'requests', MockRequests)

    assert dummy_repository.gating_recipients == ['batman', 'robin']


def test_no_gating_recipients(module, dummy_repository, monkeypatch):
    # gating configuration found
    monkeypatch.setattr(gluetool.utils, 'requests', MockRequests)
    monkeypatch.setattr(MockRequests, 'response', 'data')

    assert dummy_repository.gating_recipients == []
