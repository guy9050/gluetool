import pytest

import gluetool_modules.testing.openstack.openstack_job
import gluetool_modules.testing.beaker.beaker_job

from libci.tests.test_dispatch_job import create_build_params

from . import create_module, check_loadable


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(gluetool_modules.testing.openstack.openstack_job.OpenStackJob)


def create_openstack_build_params(mod, **kwargs):
    params = {
        'build_dependencies_options': 'some build-dependencies options',
        'install_mbs_build_options': 'some install mbs build options',
        'guess_environment_options': 'some guess-environment options',
        'wow_options': [
            'some w-t options',
            'other w-t options'
        ],
        'openstack_options': 'some openstack options',
        'test_schedule_runner_options': 'some test-schedule-runner options',
        'test_schedule_runner_restraint_options': 'some test-schedule-runner-restraint options',
        'brew_build_task_params_options': 'some brew-build options',
        'dist_git_options': 'some dist-git options'
    }

    params.update(kwargs)

    params = create_build_params(mod, **params)

    # pylint: disable=protected-access
    if mod._config.get('install-rpms-blacklist', None):
        params['brew_build_task_params_options'] = '{} --install-rpms-blacklist={}'.format(
            params['brew_build_task_params_options'], mod._config['install-rpms-blacklist'])

    params['wow_options'] = gluetool_modules.testing.openstack.openstack_job.DEFAULT_WOW_OPTIONS_SEPARATOR.join(
        params['wow_options'])

    return params


def test_sanity(module):
    # pylint: disable=unused-argument
    pass


def test_loadable(module):
    glue, _ = module

    check_loadable(glue, 'gluetool_modules/testing/openstack/openstack_job.py', 'OpenStackJob')


@pytest.mark.parametrize('rpm_blacklist', [
    None,
    'blacklisted packages'
])
def test_build_params(module_with_primary_task, rpm_blacklist):
    mod = module_with_primary_task

    # pylint: disable=protected-access
    mod._config.update({
        'install-rpms-blacklist': rpm_blacklist,
        'wow-options-separator': gluetool_modules.testing.beaker.beaker_job.DEFAULT_WOW_OPTIONS_SEPARATOR,
        'dist-git-options': 'some dist-git options',
        'install-mbs-build-options': 'some install mbs build options'
    })

    expected_params = create_openstack_build_params(mod)

    assert mod.build_params == expected_params
