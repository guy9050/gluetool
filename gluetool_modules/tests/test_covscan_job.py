import pytest

import gluetool_modules.static_analysis.covscan.covscan_job
from libci.tests.test_dispatch_job import create_build_params
from . import create_module


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(gluetool_modules.static_analysis.covscan.covscan_job.CovscanJob)


def test_sanity(module):
    # pylint: disable=unused-argument
    pass


def test_loadable(module):
    ci, _ = module

    # pylint: disable=protected-access
    python_mod = ci._load_python_module('static_analysis/covscan', 'pytest_covscan_job',
                                        'gluetool_modules/static_analysis/covscan/covscan_job.py')

    assert hasattr(python_mod, 'CovscanJob')


def test_build_params(module_with_primary_task):
    mod = module_with_primary_task

    expected_params = create_build_params(mod)

    assert mod.build_params == expected_params
