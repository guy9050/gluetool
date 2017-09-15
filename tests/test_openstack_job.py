import pytest

import libci
import libci.dispatch_job
import libci.modules.testing.openstack.openstack_job

from . import create_module
from .test_dispatch_job import create_build_params


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.testing.openstack.openstack_job.OpenStackJob)


def create_openstack_build_params(mod, **kwargs):
    params = {
        'build_dependencies_options': 'some build-dependencies options',
        'guess_product_options': 'some guess-product options',
        'guess_beaker_distro_options': 'some guess-distro options',
        'guess_openstack_image_options': 'some guess-openstack-image options',
        'wow_options': 'some w-t options',
        'restraint_runner_options': 'some restraint-runner options'
    }

    params.update(kwargs)
    return create_build_params(mod, **params)


def test_sanity(module):
    # pylint: disable=unused-argument
    pass


def test_loadable(module):
    ci, _ = module

    # pylint: disable=protected-access
    python_mod = ci._load_python_module('testing/openstack', 'pytest_openstack_job',
                                        'libci/modules/testing/openstack/openstack_job.py')

    assert hasattr(python_mod, 'OpenStackJob')


def test_build_params(module):
    _, mod = module

    expected_params = create_openstack_build_params(mod)

    assert mod.build_params == expected_params
