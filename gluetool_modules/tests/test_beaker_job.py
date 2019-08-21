import pytest

import gluetool_modules.testing.beaker.beaker_job
from libci.tests.test_dispatch_job import create_build_params
from . import create_module, check_loadable


@pytest.fixture(name='module')
def fixture_module():
    return create_module(gluetool_modules.testing.beaker.beaker_job.BeakerJob)


def create_beaker_build_params(mod, **kwargs):
    params = {
        'build_dependencies_options': 'some build-dependencies options',
        'guess_environment_options': 'some guess-environment options',
        'wow_options': [
            'some w-t options',
            'other w-t options'
        ],
        'jobwatch_options': 'some jobwatch options',
        'beaker_options': 'some beaker options',
        'brew_build_task_params_options': 'some brew-build options'
    }

    params.update(kwargs)

    params = create_build_params(mod, **params)

    if mod._config.get('install-rpms-blacklist', None):
        params['brew_build_task_params_options'] = '{} --install-rpms-blacklist={}'.format(
            params['brew_build_task_params_options'], mod._config['install-rpms-blacklist'])

    params['wow_options'] = gluetool_modules.testing.beaker.beaker_job.DEFAULT_WOW_OPTIONS_SEPARATOR.join(
        params['wow_options'])

    return params


def test_sanity(module):
    pass


def test_loadable(module):
    glue, _ = module

    check_loadable(glue, 'gluetool_modules/testing/beaker/beaker_job.py', 'BeakerJob')


@pytest.mark.parametrize('rpm_blacklist', [
    None,
    'blacklisted packages'
])
def test_build_params(module_with_primary_task, rpm_blacklist):
    mod = module_with_primary_task

    mod._config.update({
        'install-rpms-blacklist': rpm_blacklist,
        'wow-options-separator': gluetool_modules.testing.beaker.beaker_job.DEFAULT_WOW_OPTIONS_SEPARATOR
    })

    expected_params = create_beaker_build_params(mod)

    assert mod.build_params == expected_params
