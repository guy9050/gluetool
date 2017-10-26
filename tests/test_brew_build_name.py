import pytest

from mock import MagicMock
from libci.modules.helpers.jenkins.brew_build_name import CIBrewBuildName
from . import create_module, patch_shared, assert_shared


@pytest.fixture(name='module')
def fixture_module():
    return create_module(CIBrewBuildName)


def test_loadable(module):
    ci, _ = module
    # pylint: disable=protected-access
    python_mod = ci._load_python_module('helpers/jenkins', 'pytest_brew_build_name',
                                        'libci/modules/helpers/jenkins/brew_build_name.py')

    assert hasattr(python_mod, 'CIBrewBuildName')


def test_no_brew(module):
    _, module = module

    assert_shared('primary_task', module.execute)


def test_no_jenkins(module, monkeypatch):
    _, module = module

    patch_shared(monkeypatch, module, {
        'primary_task': 'dummy_task'
    })

    assert_shared('jenkins', module.execute)


def test_no_build_url(log, module, monkeypatch):
    _, module = module

    patch_shared(monkeypatch, module, {
        'jenkins': MagicMock(),
        'primary_task': 'dummy_task'
    })

    try:
        monkeypatch.delenv('BUILD_URL')
    except KeyError:
        pass

    module.execute()
    assert log.records[-1].message == '$BUILD_URL env var not found, was this job started by Jenkins?'


def test_run(log, module, monkeypatch):
    short_name = 'dummy_short_name'
    thread_id = 'dummy-thread-id'

    _, module = module

    mocked_set_build_name = MagicMock()

    patch_shared(monkeypatch, module, {
        'primary_task': MagicMock(short_name=short_name),
        'jenkins': MagicMock(set_build_name=mocked_set_build_name),
        'thread_id': thread_id
    })

    monkeypatch.setenv('BUILD_URL', 'dummy_jenkins_url')

    module.execute()
    assert log.records[-1].message == "build name set: '{}:{}'".format(thread_id, short_name)
    mocked_set_build_name.assert_called_with('{}:{}'.format(thread_id, short_name))
