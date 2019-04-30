# WARNING:
#
# This class of tests reads results and compares their text representation
# created by `notify-email` machinery with expected output. Both the results
# and the expected output is Red Hat specific, depending our custom templates.
# It should be possible to create publicly viewable data but I'd rather find
# better option than just taking existing results and "anonymize" them - way
# too much work. But in general, the test itself, given proper results and
# expected outputs, is quite generic.
#
# And the test is in the wrong repository - this one belongs to somewhere closer
# to the configuration, but so far, we don't have such infrastructure...
#

# pylint: disable=line-too-long

import os
import sys

import pytest

import gluetool
import gluetool.utils

import gluetool_modules.libs.artifacts

import gluetool_modules.testing.testing_results
import gluetool_modules.helpers.notify_email.notify_email
import gluetool_modules.helpers.dashboard

import gluetool_modules.static_analysis.covscan.covscan
import gluetool_modules.static_analysis.rpmdiff.rpmdiff
import gluetool_modules.testing.test_schedule_runner_restraint
import gluetool_modules.helpers.notify_email.notify_email_rpmdiff_formatter
import gluetool_modules.helpers.notify_email.notify_email_covscan_formatter
import gluetool_modules.helpers.notify_email.notify_email_beah_formatter
import gluetool_modules.helpers.install_koji_build

import gluetool_modules.build_on_commit.build_on_commit
import gluetool_modules.infrastructure.koji_fedora
import gluetool_modules.testing.beaker.beaker
import gluetool_modules.testing.wow

from mock import MagicMock

from . import create_module, patch_shared


sys.modules['gluetool.glue.static_analysis/covscan-covscan'] = sys.modules['gluetool_modules.static_analysis.covscan.covscan']
sys.modules['gluetool.glue.static_analysis/rpmdiff-rpmdiff'] = sys.modules['gluetool_modules.static_analysis.rpmdiff.rpmdiff']
sys.modules['gluetool.glue/testing-test_schedule_runner_restraint'] = sys.modules['gluetool_modules.testing.test_schedule_runner_restraint']
sys.modules['gluetool.glue.testing/beaker-beaker'] = sys.modules['gluetool_modules.testing.beaker.beaker']


def asset(filename):
    return os.path.join('gluetool_modules', 'tests', 'assets', 'notify-email-templates', filename)


@pytest.fixture(name='notify_email')
def fixture_notify_email(integration_config, monkeypatch):
    integration_config = os.path.abspath(integration_config)

    notify_email = create_module(gluetool_modules.helpers.notify_email.notify_email.Notify, name='notify-email')[1]
    glue = notify_email.glue

    # pylint: disable=protected-access
    glue._config['module-config-path'] = [os.path.join(integration_config, 'config')]

    notify_email.parse_config()

    dashboard = create_module(gluetool_modules.helpers.dashboard.Dashboard, name='dashboard', glue=glue)[1]
    dashboard.parse_config()

    mock_task = MagicMock(ARTIFACT_NAMESPACE='brew-build', id=123456,
                          nvr='dummy-package-1.2.3-79.el7', owner='foo',
                          issuer='bar', branch='fixing-bz17', target='release-candidate')

    patch_shared(monkeypatch, dashboard, {
        'eval_context': {
            'PRIMARY_TASK': mock_task
        }
    })

    patch_shared(monkeypatch, notify_email, {
        'eval_context': {
            'PRIMARY_TASK': mock_task,
            'DASHBOARD_URL': dashboard.dashboard_url
        },
        'primary_task': mock_task,
        'notification_recipients': ['dummy-recipient']
    })

    return notify_email


@pytest.fixture(name='result_modules', params=[
    # result_format, result_source, expected_message, extra_modules, extra_env, extra_config
    (
        'json',
        asset('covscan-pass.json'),
        asset('covscan-pass.yaml'),
        {
            'notify-email-covscan-formatter': gluetool_modules.helpers.notify_email.notify_email_covscan_formatter.NotifyEmailCovscanFormatter
        },
        {}, {}
    ),
    (
        'json',
        asset('covscan-fail.json'),
        asset('covscan-fail.yaml'),
        {
            'notify-email-covscan-formatter': gluetool_modules.helpers.notify_email.notify_email_covscan_formatter.NotifyEmailCovscanFormatter
        },
        {}, {}
    ),
    (
        'json',
        asset('rpmdiff-analysis-needs-inspection.json'),
        asset('rpmdiff-analysis-needs-inspection.yaml'),
        {
            'notify-email-rpmdiff-formatter': gluetool_modules.helpers.notify_email.notify_email_rpmdiff_formatter.NotifyEmailRPMdiffFormatter
        },
        {}, {}
    ),
    (
        'json',
        asset('rpmdiff-analysis-fail.json'),
        asset('rpmdiff-analysis-fail.yaml'),
        {
            'notify-email-rpmdiff-formatter': gluetool_modules.helpers.notify_email.notify_email_rpmdiff_formatter.NotifyEmailRPMdiffFormatter
        },
        {}, {}
    ),
    (
        'json',
        asset('rpmdiff-analysis-info.json'),
        asset('rpmdiff-analysis-info.yaml'),
        {
            'notify-email-rpmdiff-formatter': gluetool_modules.helpers.notify_email.notify_email_rpmdiff_formatter.NotifyEmailRPMdiffFormatter
        },
        {}, {}
    ),
    (
        'json',
        asset('rpmdiff-analysis-pass.json'),
        asset('rpmdiff-analysis-pass.yaml'),
        {
            'notify-email-rpmdiff-formatter': gluetool_modules.helpers.notify_email.notify_email_rpmdiff_formatter.NotifyEmailRPMdiffFormatter
        },
        {}, {}
    ),
    (
        'json',
        asset('restraint-pass.json'),
        asset('restraint-pass.yaml'),
        {
            'notify-email-beah-formatter': gluetool_modules.helpers.notify_email.notify_email_beah_formatter.NotifyEmailBeahFormatter
        },
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        },
        {
            'add-frontend-url': True
        }
    ),
    (
        'json',
        asset('restraint-pass.json'),
        asset('restraint-pass-reservation.yaml'),
        {
            'notify-email-beah-formatter': gluetool_modules.helpers.notify_email.notify_email_beah_formatter.NotifyEmailBeahFormatter
        },
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        },
        {
            'add-frontend-url': True,
            'add-reservation': True
        }
    ),
    (
        'json',
        asset('restraint-fail.json'),
        asset('restraint-fail.yaml'),
        {
            'notify-email-beah-formatter': gluetool_modules.helpers.notify_email.notify_email_beah_formatter.NotifyEmailBeahFormatter
        },
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        },
        {
            'add-frontend-url': True,
            'add-reservation': False
        }
    ),
    (
        'json',
        asset('restraint-fail.json'),
        asset('restraint-fail-reservation.yaml'),
        {
            'notify-email-beah-formatter': gluetool_modules.helpers.notify_email.notify_email_beah_formatter.NotifyEmailBeahFormatter
        },
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        },
        {
            'add-frontend-url': True,
            'add-reservation': True
        }
    ),
    # beaker is basically a restraint - just excercise loading of beaker results
    (
        'json',
        asset('beaker-pass.json'),
        asset('beaker-pass.yaml'),
        {
            'notify-email-beah-formatter': gluetool_modules.helpers.notify_email.notify_email_beah_formatter.NotifyEmailBeahFormatter
        },
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        },
        {
            'add-frontend-url': True
        }
    )
])
def fixture_result_modules(request, notify_email, monkeypatch):
    result_format, result_source, expected_message, extra_modules, extra_env, extra_config = request.param

    result_source = os.path.abspath(result_source)
    expected_message = os.path.abspath(expected_message)

    for name, value in extra_env.iteritems():
        monkeypatch.setenv(name, value)

    glue = notify_email.glue

    testing_results = create_module(gluetool_modules.testing.testing_results.TestingResults, glue=glue,
                                    name='testing-results')[1]

    # pylint: disable=protected-access
    testing_results._config['init-file'] = '{}:{}'.format(result_format, result_source)
    testing_results.execute()

    notify_email._config.update(extra_config)

    expected_message = gluetool.utils.load_yaml(expected_message)

    for name, klass in extra_modules.iteritems():
        module = create_module(klass, glue=glue, name=name)[1]
        module.parse_config()
        module.add_shared()

    return testing_results, notify_email, expected_message


@pytest.fixture(name='soft_modules', params=[
    (
        gluetool_modules.testing.beaker.beaker.BeakerJobwatchError,
        (MagicMock(), 'beaker matrix URL',),
        {},
        asset('BeakerJobwatchError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        gluetool_modules.build_on_commit.build_on_commit.BocBuildError,
        ('a branch "foo"', 'a component "bar"', 'a target "baz"', 'some task URL'),
        {},
        asset('BocBuildError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        gluetool_modules.static_analysis.covscan.covscan.CovscanFailedError,
        ('covscan task URL',),
        {},
        asset('CovscanFailedError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        gluetool.utils.IncompatibleOptionsError,
        ('option --foo does not work when --bar is set',),
        {},
        asset('IncompatibleOptionsError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        gluetool_modules.libs.artifacts.NoArtifactsError,
        ('dummy task ID',),
        {},
        asset('NoArtifactsError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        gluetool_modules.helpers.install_koji_build.SUTInstallationFailedError,
        (None, 'some install logs URL',),
        {},
        asset('SUTInstallationFailedError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        gluetool_modules.testing.wow.NoTestAvailableError,
        (MagicMock(),),
        {},
        asset('NoTestAvailableError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    )
])
def fixture_soft_modules(request, notify_email, integration_config, monkeypatch):
    integration_config = os.path.abspath(integration_config)

    soft_klass, args, kwargs, expected_message, extra_env = request.param

    for name, value in extra_env.iteritems():
        monkeypatch.setenv(name, value)

    # pylint: disable=protected-access
    notify_email._config['add-frontend-url'] = True

    exc = soft_klass(*args, **kwargs)
    dummy_tb = [
        ['tests/test_notify_email_templates.py', 13, 'C', 'return B()'],
        ['tests/test_notify_email_templates.py', 17, 'B', 'return A()'],
        ['tests/test_notify_email_templates.py', 19, 'A', 'raise {}'.format(soft_klass.__name__)]
    ]

    failure = gluetool.glue.Failure(notify_email, (soft_klass, exc, dummy_tb))

    message = gluetool.utils.load_yaml(os.path.abspath(os.path.join('gluetool_modules', 'tests', 'assets',
                                                                    'notify-email-templates', 'soft-error-base.yaml')))
    message.update(gluetool.utils.load_yaml(os.path.abspath(expected_message)))

    return failure, notify_email, message


def _test_message(notify_email, msg, expected):
    gluetool.log.log_blob(notify_email.debug, 'rendered subject', msg.subject)
    assert msg.subject == expected['subject']

    gluetool.log.log_blob(notify_email.debug, 'rendered body', msg.body)
    assert msg.body == expected['body']

    gluetool.log.log_blob(notify_email.debug, 'rendered header', msg.header)
    assert msg.header == expected['header']

    gluetool.log.log_blob(notify_email.debug, 'rendered footer', msg.footer)
    assert msg.footer == expected['footer']

    assert msg.recipients == expected['recipients']
    assert msg.cc == expected['cc']
    assert msg.bcc == expected['bcc']
    assert msg.sender == expected['sender']


@pytest.mark.integration
def test_result(result_modules):
    testing_results, notify_email, expected = result_modules

    # pylint: disable=protected-access
    msg = notify_email._format_result(testing_results._results[0])

    _test_message(notify_email, msg, expected)


@pytest.mark.integration
def test_failure(soft_modules):
    failure, notify_email, expected_message = soft_modules

    # pylint: disable=protected-access
    msg = notify_email._format_failure(failure)

    _test_message(notify_email, msg, expected_message)
