import pytest

import gluetool_modules.testing.openstack.openstack_job

from libci.tests.test_dispatch_job import create_build_params

from . import create_module


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(gluetool_modules.testing.openstack.openstack_job.OpenStackJob)


def create_openstack_build_params(mod, **kwargs):
    params = {
        'build_dependencies_options': 'some build-dependencies options',
        'guess_product_options': 'some guess-product options',
        'guess_beaker_distro_options': 'some guess-distro options',
        'guess_openstack_image_options': 'some guess-openstack-image options',
        'wow_options': 'some w-t options',
        'openstack_options': 'some openstack options',
        'restraint_scheduler_options': 'some restraint-scheduler options',
        'restraint_runner_options': 'some restraint-runner options'
    }

    params.update(kwargs)

    params = create_build_params(mod, **params)

    # pylint: disable=protected-access
    if mod._config.get('install-rpms-blacklist', None):
        params['restraint_scheduler_options'] = '{} --install-rpms-blacklist={}'.format(
            params['restraint_scheduler_options'], mod._config['install-rpms-blacklist'])

    return params


def test_sanity(module):
    # pylint: disable=unused-argument
    pass


def test_loadable(module):
    ci, _ = module

    # pylint: disable=protected-access
    python_mod = ci._load_python_module('testing/openstack', 'pytest_openstack_job',
                                        'gluetool_modules/testing/openstack/openstack_job.py')

    assert hasattr(python_mod, 'OpenStackJob')


@pytest.mark.parametrize('rpm_blacklist', [
    None,
    'blacklisted packages'
])
def test_build_params(module_with_primary_task, rpm_blacklist):
    mod = module_with_primary_task

    # pylint: disable=protected-access
    mod._config['install-rpms-blacklist'] = rpm_blacklist
    expected_params = create_openstack_build_params(mod)

    assert mod.build_params == expected_params
