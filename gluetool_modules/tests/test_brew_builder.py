import pytest
import gluetool_modules.libs
from mock import MagicMock
from gluetool_modules.testing.pull_request_builder import brew_builder
from gluetool import GlueCommandError
from . import create_module, patch_shared, check_loadable

RHPKG_OUTPUT = """
Created task: 123
Task info: dummy_brew_url
"""


@pytest.fixture(name='module')
def fixture_module():
    return create_module(brew_builder.BrewBuilder)[1]


def test_loadable(module):
    check_loadable(module.glue, 'gluetool_modules/testing/pull_request_builder/brew_builder.py', 'BrewBuilder')


def test_pass(module, monkeypatch):
    init_mock = MagicMock(return_value=None)
    run_mock = MagicMock(return_value=MagicMock(stdout=RHPKG_OUTPUT))
    monkeypatch.setattr(brew_builder.Command, '__init__', init_mock)
    monkeypatch.setattr(brew_builder.Command, 'run', run_mock)

    patch_shared(monkeypatch, module, {
        'src_rpm': MagicMock()
    })

    publish_result_mock = MagicMock()
    monkeypatch.setattr(brew_builder, 'publish_result', publish_result_mock)

    module.execute()

    publish_result_mock.assert_called_once_with(module, brew_builder.BrewBuildTestResult,
                                                'PASS', 'dummy_brew_url', None, None)


def test_fail(module, monkeypatch):
    process_output_mock = MagicMock(exit_code=1)

    class MockedCommand(object):
        def __init__(self, command, *args, **kwargs):
            self.cmd = command

        def run(self, *args, **kwargs):
            if self.cmd[0] == 'rhpkg':
                return MagicMock(stdout=RHPKG_OUTPUT)
            elif self.cmd[0] == 'brew':
                raise GlueCommandError(self.cmd, process_output_mock)
            else:
                return MagicMock()

    monkeypatch.setattr(brew_builder, 'Command', MockedCommand)

    patch_shared(monkeypatch, module, {
        'src_rpm': MagicMock()
    })

    publish_result_mock = MagicMock()
    monkeypatch.setattr(brew_builder, 'publish_result', publish_result_mock)

    module.execute()

    publish_result_mock.assert_called_once_with(module, brew_builder.BrewBuildTestResult,
                                                'FAIL', None, 'Wait for brew build finish failed', process_output_mock)


def test_fail_src_rpm(module, monkeypatch):
    process_output_mock = MagicMock(exit_code=1)

    init_mock = MagicMock(return_value=None)
    run_mock = MagicMock(return_value=MagicMock(stdout=RHPKG_OUTPUT))
    monkeypatch.setattr(brew_builder.Command, '__init__', init_mock)
    monkeypatch.setattr(brew_builder.Command, 'run', run_mock)

    src_rpm_mock = MagicMock(
        side_effect=gluetool_modules.libs.brew_build_fail.BrewBuildFailedError(
            'src_rpm build failed',
            process_output_mock
        )
    )

    patch_shared(monkeypatch, module, {}, callables={
        'src_rpm': src_rpm_mock
    })

    publish_result_mock = MagicMock()
    monkeypatch.setattr(brew_builder, 'publish_result', publish_result_mock)

    module.execute()

    publish_result_mock.assert_called_once_with(module, brew_builder.BrewBuildTestResult,
                                                'FAIL', None, 'src_rpm build failed', process_output_mock)
