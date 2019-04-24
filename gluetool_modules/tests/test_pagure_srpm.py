import pytest

from mock import MagicMock
from mock import call

import os
import __builtin__
import gluetool
from gluetool_modules.helpers import pagure_srpm
from . import create_module, patch_shared


@pytest.fixture(name='module')
def fixture_module():
    module = create_module(pagure_srpm.PagureSRPM)[1]

    return module


def test_loadable(module):
    ci = module.glue
    python_mod = ci._load_python_module('helpers/pagure_srpm', 'pytest_pagure_srpm',
                                        'gluetool_modules/helpers/pagure_srpm.py')

    assert hasattr(python_mod, 'PagureSRPM')


def run_src_rpm(module, monkeypatch, command_calls):
    project_mock = MagicMock()
    project_mock.name = 'dummy_project_name'
    project_mock.clone_url = 'dummy_clone_url'

    pull_request_id_mock = MagicMock(repository_pr_id=8)

    pull_request_mock = MagicMock()
    pull_request_mock.ARTIFACT_NAMESPACE = 'dist-git-pr'
    pull_request_mock.project = project_mock
    pull_request_mock.pull_request_id = pull_request_id_mock
    pull_request_mock.destination_branch = 'dummy_destination_branch'

    patch_shared(monkeypatch, module, {
        'primary_task': pull_request_mock
    })

    run_return_value_mock = MagicMock()
    run_return_value_mock.stdout = 'dummy_directory/dummy_src_rpm.srpm'
    init_mock = MagicMock(return_value=None)
    run_mock = MagicMock(return_value=run_return_value_mock)
    monkeypatch.setattr(pagure_srpm.Command, '__init__', init_mock)
    monkeypatch.setattr(pagure_srpm.Command, 'run', run_mock)

    chdir_mock = MagicMock()
    monkeypatch.setattr(os, 'chdir', chdir_mock)

    rename_mock = MagicMock()
    monkeypatch.setattr(os, 'rename', rename_mock)

    monkeypatch.setattr(__builtin__, 'open', MagicMock())

    assert module.src_rpm() == 'dummy_src_rpm.srpm'
    chdir_mock.assert_called_once_with('dummy_project_name')
    rename_mock.assert_called_once_with('dummy_project_name.spec', 'dummy_project_name.spec.backup')

    init_mock.assert_has_calls(command_calls, any_order=True)


def test_src_rpm(module, monkeypatch):
    calls = []

    calls.append(call(['git', 'clone', '-b', 'dummy_destination_branch', 'dummy_clone_url'], logger=module.logger))
    calls.append(call(['git', 'fetch', 'origin', 'refs/pull/8/head'], logger=module.logger))
    calls.append(call(['git', 'merge', 'FETCH_HEAD', '-m', 'ci pr merge'], logger=module.logger))
    calls.append(call(['rhpkg', 'srpm'], logger=module.logger))

    run_src_rpm(module, monkeypatch, calls)


def test_src_rpm_additional_options(module, monkeypatch):
    calls = []

    module._config['git-clone-options'] = '--depth 1'
    module._config['git-fetch-options'] = '--multiple'
    module._config['git-merge-options'] = '--allow-unrelated-histories'

    calls.append(call(
        ['git', 'clone', '-b', 'dummy_destination_branch', 'dummy_clone_url', '--depth', '1'],
        logger=module.logger
    ))
    calls.append(call(
        ['git', 'fetch', 'origin', 'refs/pull/8/head', '--multiple'],
        logger=module.logger
    ))
    calls.append(call(
        ['git', 'merge', 'FETCH_HEAD', '-m', 'ci pr merge', '--allow-unrelated-histories'],
        logger=module.logger
    ))
    calls.append(call(['rhpkg', 'srpm'], logger=module.logger))

    run_src_rpm(module, monkeypatch, calls)


def test_incompatible_type(module, monkeypatch):
    pull_request_mock = MagicMock()
    pull_request_mock.ARTIFACT_NAMESPACE = 'unsupported-artifact'

    patch_shared(monkeypatch, module, {
        'primary_task': pull_request_mock
    })

    with pytest.raises(gluetool.GlueError, match=r"^Incompatible artifact namespace: unsupported-artifact$"):
        module.src_rpm()
