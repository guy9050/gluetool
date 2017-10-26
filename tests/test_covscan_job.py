import pytest

import libci
import libci.dispatch_job
import libci.modules.static_analysis.covscan.covscan_job

from . import create_module
from .test_dispatch_job import create_build_params


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.static_analysis.covscan.covscan_job.CovscanJob)


def test_sanity(module):
    # pylint: disable=unused-argument
    pass


def test_loadable(module):
    ci, _ = module

    # pylint: disable=protected-access
    python_mod = ci._load_python_module('static_analysis/covscan', 'pytest_covscan_job',
                                        'libci/modules/static_analysis/covscan/covscan_job.py')

    assert hasattr(python_mod, 'CovscanJob')


def test_build_params(module):
    _, mod = module

    expected_params = create_build_params(mod)

    assert mod.build_params == expected_params
