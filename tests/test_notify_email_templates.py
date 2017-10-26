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

import os
import sys
import traceback

import pytest

from . import create_module, patch_shared

import libci.modules.testing.testing_results
import libci.modules.helpers.notify_email.notify_email

import libci.modules.static_analysis.covscan.covscan
import libci.modules.static_analysis.rpmdiff.rpmdiff
import libci.modules.testing.restraint.runner
import libci.modules.helpers.notify_email.notify_email_rpmdiff_formatter
import libci.modules.helpers.notify_email.notify_email_covscan_formatter
import libci.modules.helpers.notify_email.notify_email_beah_formatter

import libci.modules.build_on_commit.build_on_commit
import libci.modules.infrastructure.koji_fedora
import libci.modules.static_analysis.covscan.covscan
import libci.modules.testing.beaker.beaker
import libci.modules.testing.restraint.scheduler
import libci.modules.testing.wow
import libci.utils

sys.modules['libci.ci.static_analysis/covscan-covscan'] = sys.modules['libci.modules.static_analysis.covscan.covscan']
sys.modules['libci.ci.static_analysis/rpmdiff-rpmdiff'] = sys.modules['libci.modules.static_analysis.rpmdiff.rpmdiff']
sys.modules['libci.ci.testing/restraint-runner'] = sys.modules['libci.modules.testing.restraint.runner']


@pytest.fixture(name='notify_email')
def fixture_notify_email(integration_config, monkeypatch, tmpdir):
    integration_config = os.path.abspath(integration_config)

    notify_email = create_module(libci.modules.helpers.notify_email.notify_email.Notify, name='notify-email')[1]
    CI = notify_email.ci

    CI._config['module-config-path'] = [os.path.join(integration_config, 'config')]

    notify_email.parse_config()

    patch_shared(monkeypatch, notify_email, {
        'primary_task': libci.utils.Bunch(task_id=123456, nvr='dummy-package-1.2.3-79.el7', owner='foo',
                                          issuer='bar', branch='fixing-bz17', target='release-candidate'),
        'notification_recipients': ['dummy-recipient']
    })

    return notify_email


@pytest.fixture(name='result_modules', params=[
    # result_format, result_source, expected_message, extra_modules, extra_env, extra_config
    (
        'json',
        os.path.join('tests', 'assets', 'notify-email-templates', 'covscan-pass.json'),
        os.path.join('tests', 'assets', 'notify-email-templates', 'covscan-pass.yaml'),
        {
            'notify-email-covscan-formatter': libci.modules.helpers.notify_email.notify_email_covscan_formatter.NotifyEmailCovscanFormatter
        },
        {}, {}
    ),
    (
        'json',
        os.path.join('tests', 'assets', 'notify-email-templates', 'covscan-fail.json'),
        os.path.join('tests', 'assets', 'notify-email-templates', 'covscan-fail.yaml'),
        {
            'notify-email-covscan-formatter': libci.modules.helpers.notify_email.notify_email_covscan_formatter.NotifyEmailCovscanFormatter
        },
        {}, {}
    ),
    (
        'json',
        os.path.join('tests', 'assets', 'notify-email-templates', 'rpmdiff-analysis-needs-inspection.json'),
        os.path.join('tests', 'assets', 'notify-email-templates', 'rpmdiff-analysis-needs-inspection.yaml'),
        {
            'notify-email-rpmdiff-formatter': libci.modules.helpers.notify_email.notify_email_rpmdiff_formatter.NotifyEmailRPMdiffFormatter
        },
        {}, {}
    ),
    (
        'json',
        os.path.join('tests', 'assets', 'notify-email-templates', 'rpmdiff-analysis-fail.json'),
        os.path.join('tests', 'assets', 'notify-email-templates', 'rpmdiff-analysis-fail.yaml'),
        {
            'notify-email-rpmdiff-formatter': libci.modules.helpers.notify_email.notify_email_rpmdiff_formatter.NotifyEmailRPMdiffFormatter
        },
        {}, {}
    ),
    (
        'json',
        os.path.join('tests', 'assets', 'notify-email-templates', 'rpmdiff-analysis-info.json'),
        os.path.join('tests', 'assets', 'notify-email-templates', 'rpmdiff-analysis-info.yaml'),
        {
            'notify-email-rpmdiff-formatter': libci.modules.helpers.notify_email.notify_email_rpmdiff_formatter.NotifyEmailRPMdiffFormatter
        },
        {}, {}
    ),
    (
        'json',
        os.path.join('tests', 'assets', 'notify-email-templates', 'rpmdiff-analysis-pass.json'),
        os.path.join('tests', 'assets', 'notify-email-templates', 'rpmdiff-analysis-pass.yaml'),
        {
            'notify-email-rpmdiff-formatter': libci.modules.helpers.notify_email.notify_email_rpmdiff_formatter.NotifyEmailRPMdiffFormatter
        },
        {}, {}
    ),
    (
        'json',
        os.path.join('tests', 'assets', 'notify-email-templates', 'restraint-pass.json'),
        os.path.join('tests', 'assets', 'notify-email-templates', 'restraint-pass.yaml'),
        {
            'notify-email-beah-formatter': libci.modules.helpers.notify_email.notify_email_beah_formatter.NotifyEmailBeahFormatter
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
        os.path.join('tests', 'assets', 'notify-email-templates', 'restraint-pass.json'),
        os.path.join('tests', 'assets', 'notify-email-templates', 'restraint-pass-reservation.yaml'),
        {
            'notify-email-beah-formatter': libci.modules.helpers.notify_email.notify_email_beah_formatter.NotifyEmailBeahFormatter
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
        os.path.join('tests', 'assets', 'notify-email-templates', 'restraint-fail.json'),
        os.path.join('tests', 'assets', 'notify-email-templates', 'restraint-fail.yaml'),
        {
            'notify-email-beah-formatter': libci.modules.helpers.notify_email.notify_email_beah_formatter.NotifyEmailBeahFormatter
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
        os.path.join('tests', 'assets', 'notify-email-templates', 'restraint-fail.json'),
        os.path.join('tests', 'assets', 'notify-email-templates', 'restraint-fail-reservation.yaml'),
        {
            'notify-email-beah-formatter': libci.modules.helpers.notify_email.notify_email_beah_formatter.NotifyEmailBeahFormatter
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
        os.path.join('tests', 'assets', 'notify-email-templates', 'beaker-pass.json'),
        os.path.join('tests', 'assets', 'notify-email-templates', 'beaker-pass.yaml'),
        {
            'notify-email-beah-formatter': libci.modules.helpers.notify_email.notify_email_beah_formatter.NotifyEmailBeahFormatter
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
def fixture_result_modules(request, notify_email, monkeypatch, tmpdir):
    result_format, result_source, expected_message, extra_modules, extra_env, extra_config = request.param

    result_source = os.path.abspath(result_source)
    expected_message = os.path.abspath(expected_message)

    for name, value in extra_env.iteritems():
        monkeypatch.setenv(name, value)

    CI = notify_email.ci

    testing_results = create_module(libci.modules.testing.testing_results.TestingResults, ci=CI,
                                    name='testing-results')[1]

    testing_results._config['init-file'] = '{}:{}'.format(result_format, result_source)
    testing_results.execute()

    notify_email._config.update(extra_config)

    expected_message = libci.utils.load_yaml(expected_message)

    for name, klass in extra_modules.iteritems():
        module = create_module(klass, ci=CI, name=name)[1]
        module.parse_config()
        module.add_shared()

    return testing_results, notify_email, expected_message


@pytest.fixture(name='soft_modules', params=[
    (
        libci.ci.CIError,
        ('dummy error message',),
        {},
        os.path.join('tests', 'assets', 'notify-email-templates', 'hard-error.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        libci.modules.testing.beaker.beaker.BeakerJobwatchError,
        ('beaker matrix URL',),
        {},
        os.path.join('tests', 'assets', 'notify-email-templates', 'BeakerJobwatchError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        libci.modules.build_on_commit.build_on_commit.BocBuildError,
        ('a branch "foo"', 'a component "bar"', 'a target "baz"', 'some task URL'),
        {},
        os.path.join('tests', 'assets', 'notify-email-templates', 'BocBuildError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        libci.modules.static_analysis.covscan.covscan.CovscanFailedError,
        ('covscan task URL',),
        {},
        os.path.join('tests', 'assets', 'notify-email-templates', 'CovscanFailedError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        libci.utils.IncompatibleOptionsError,
        ('option --foo does not work when --bar is set',),
        {},
        os.path.join('tests', 'assets', 'notify-email-templates', 'IncompatibleOptionsError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        libci.modules.infrastructure.koji_fedora.NoArtifactsError,
        ('dummy task ID',),
        {},
        os.path.join('tests', 'assets', 'notify-email-templates', 'NoArtifactsError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        libci.modules.testing.restraint.scheduler.SUTInstallationFailedError,
        ('some install logs URL',),
        {},
        os.path.join('tests', 'assets', 'notify-email-templates', 'SUTInstallationFailedError.yaml'),
        {
            'JOB_NAME': 'ci-openstack',
            'BUILD_ID': '2692'
        }
    ),
    (
        libci.modules.testing.wow.NoTestAvailableError,
        (),
        {},
        os.path.join('tests', 'assets', 'notify-email-templates', 'NoTestAvailableError.yaml'),
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

    notify_email._config['add-frontend-url'] = True

    exc = soft_klass(*args, **kwargs)
    tb = [
        ['tests/test_notify_email_templates.py', 13, 'C', 'return B()'],
        ['tests/test_notify_email_templates.py', 17, 'B', 'return A()'],
        ['tests/test_notify_email_templates.py', 19, 'A', 'raise {}'.format(soft_klass.__name__)]
    ]

    failure = libci.ci.Failure(notify_email, (soft_klass, exc, tb))

    message = libci.utils.load_yaml(os.path.abspath(os.path.join('tests', 'assets', 'notify-email-templates',
                                                                 'soft-error-base.yaml')))
    message.update(libci.utils.load_yaml(os.path.abspath(expected_message)))

    return failure, notify_email, message


def _test_message(notify_email, msg, expected):
    libci.log.log_blob(notify_email.debug, 'rendered subject', msg.subject)
    assert msg.subject == expected['subject']

    libci.log.log_blob(notify_email.debug, 'rendered body', msg.body)
    assert msg.body == expected['body']

    libci.log.log_blob(notify_email.debug, 'rendered header', msg.header)
    assert msg.header == expected['header']

    libci.log.log_blob(notify_email.debug, 'rendered footer', msg.footer)
    assert msg.footer == expected['footer']

    assert msg.recipients == expected['recipients']
    assert msg.cc == expected['cc']
    assert msg.sender == expected['sender']


@pytest.mark.integration
def test_result(result_modules):
    testing_results, notify_email, expected = result_modules

    msg = notify_email._format_result(testing_results._results[0])

    _test_message(notify_email, msg, expected)


@pytest.mark.integration
def test_failure(soft_modules):
    failure, notify_email, expected_message = soft_modules

    msg = notify_email._format_failure(failure)

    _test_message(notify_email, msg, expected_message)
