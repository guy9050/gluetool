import pytest

import gluetool_modules.testing.beaker.beaker_job
from libci.tests.test_dispatch_job import create_build_params
from . import create_module


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(gluetool_modules.testing.beaker.beaker_job.BeakerJob)


def create_beaker_build_params(mod, **kwargs):
    params = {
        'build_dependencies_options': 'some build-dependencies options',
        'guess_product_options': 'some guess-product options',
        'guess_distro_options': 'some guess-distro options',
        'wow_options': 'some w-t options',
        'jobwatch_options': 'some jobwatch options',
        'beaker_options': 'some beaker options',
        'brew_build_task_params_options': 'some brew-build options'
    }

    params.update(kwargs)

    params = create_build_params(mod, **params)

    # pylint: disable=protected-access
    if mod._config.get('install-rpms-blacklist', None):
        # pylint: disable=line-too-long
        params['brew_build_task_params_options'] = '{} --install-rpms-blacklist={}'.format(
            params['brew_build_task_params_options'], mod._config['install-rpms-blacklist'])

    return params


def test_sanity(module):
    # pylint: disable=unused-argument
    pass


def test_loadable(module):
    ci, _ = module

    # pylint: disable=protected-access
    python_mod = ci._load_python_module('testing/beaker', 'pytest_beaker_job',
                                        'gluetool_modules/testing/beaker/beaker_job.py')

    assert hasattr(python_mod, 'BeakerJob')


@pytest.mark.parametrize('rpm_blacklist', [
    None,
    'blacklisted packages'
])
def test_build_params(module_with_primary_task, rpm_blacklist):
    mod = module_with_primary_task

    # pylint: disable=protected-access
    mod._config['install-rpms-blacklist'] = rpm_blacklist
    expected_params = create_beaker_build_params(mod)

    assert mod.build_params == expected_params
