import pytest

import gluetool_modules.static_analysis.covscan.covscan_job
from gluetool_modules.tests.test_dispatch_job import create_build_params
from . import create_module, check_loadable


@pytest.fixture(name='module')
def fixture_module():
    return create_module(gluetool_modules.static_analysis.covscan.covscan_job.CovscanJob)


def test_sanity(module):
    pass


def test_loadable(module):
    glue, _ = module

    check_loadable(glue, 'gluetool_modules/static_analysis/covscan/covscan_job.py', 'CovscanJob')


def test_build_params(module_with_primary_task):
    mod = module_with_primary_task

    expected_params = create_build_params(mod)

    assert mod.build_params == expected_params
