import pytest
from mock import MagicMock

import libci
import libci.dispatch_job
import libci.modules.testing.beaker.beaker_job

from . import create_module
from .test_dispatch_job import create_build_params


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.testing.beaker.beaker_job.BeakerJob)


def create_beaker_build_params(mod, **kwargs):
    params = {
        'guess_product_options': 'some guess-product options',
        'guess_distro_options': 'some guess-distro options',
        'wow_options': 'some w-t options',
        'jobwatch_options': 'some jobwatch options'
    }

    params.update(kwargs)
    return create_build_params(mod, **params)


def test_sanity(module):
    # pylint: disable=unused-argument
    pass


def test_loadable(module):
    ci, _ = module

    # pylint: disable=protected-access
    python_mod = ci._load_python_module('testing/beaker', 'pytest_beaker_job',
                                        'libci/modules/testing/beaker/beaker_job.py')

    assert hasattr(python_mod, 'BeakerJob')


def test_build_params(module_with_primary_task, monkeypatch):
    mod = module_with_primary_task

    expected_params = create_beaker_build_params(mod)

    assert mod.build_params == expected_params
