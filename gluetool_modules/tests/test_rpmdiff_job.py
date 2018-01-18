import pytest

import gluetool_modules.static_analysis.rpmdiff.rpmdiff_job

from libci.tests.test_dispatch_job import create_build_params, test_dispatch as basic_test_dispatch
from . import create_module


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(gluetool_modules.static_analysis.rpmdiff.rpmdiff_job.RpmdiffJob)


def test_sanity(module):
    # pylint: disable=unused-argument
    pass


def test_loadable(module):
    ci, _ = module

    # pylint: disable=protected-access
    python_mod = ci._load_python_module('static_analysis/rpmdiff', 'pytest_rpmdiff_job',
                                        'gluetool_modules/static_analysis/rpmdiff/rpmdiff_job.py')

    assert hasattr(python_mod, 'RpmdiffJob')


def test_build_params(module_with_primary_task):
    mod = module_with_primary_task

    expected_params = create_build_params(mod)

    assert mod.build_params == expected_params


def test_dispatch_analysis(module, monkeypatch):
    _, mod = module

    # pylint: disable=protected-access
    mod._config['type'] = 'analysis'

    basic_test_dispatch(mod, monkeypatch, job_name='ci-rpmdiff-analysis')


def test_dispatch_comparison(module, monkeypatch):
    _, mod = module

    # pylint: disable=protected-access
    mod._config['type'] = 'comparison'

    basic_test_dispatch(mod, monkeypatch, job_name='ci-rpmdiff-comparison')