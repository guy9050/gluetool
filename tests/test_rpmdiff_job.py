import pytest

import libci
import libci.dispatch_job
import libci.modules.static_analysis.rpmdiff.rpmdiff_job

from . import create_module
from .test_dispatch_job import create_build_params, test_dispatch as basic_test_dispatch


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.static_analysis.rpmdiff.rpmdiff_job.RpmdiffJob)


def test_sanity(module):
    # pylint: disable=unused-argument
    pass


def test_loadable(module):
    ci, _ = module

    # pylint: disable=protected-access
    python_mod = ci._load_python_module('static_analysis/rpmdiff', 'pytest_rpmdiff_job',
                                        'libci/modules/static_analysis/rpmdiff/rpmdiff_job.py')

    assert hasattr(python_mod, 'RpmdiffJob')


def test_build_params(module):
    _, mod = module

    expected_params = create_build_params(mod)

    assert mod.build_params == expected_params


def test_dispatch_analysis(module, monkeypatch):
    _, mod = module

    # pylint: disable=protected-access
    mod._config['type'] = 'analysis'

    basic_test_dispatch(module, monkeypatch, job_name='ci-rpmdiff-analysis')


def test_dispatch_comparison(module, monkeypatch):
    _, mod = module

    # pylint: disable=protected-access
    mod._config['type'] = 'comparison'

    basic_test_dispatch(module, monkeypatch, job_name='ci-rpmdiff-comparison')
