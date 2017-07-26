# pylint: disable=protected-access
import os
import gzip
import pytest

from mock import MagicMock
import libci
import libci.utils
from libci.modules.static_analysis.covscan.covscan import CICovscan, CovscanResult, \
    CovscanFailedError, NoCovscanBaselineFoundError
from . import create_module

ADDED_PASS = """
{
    "defects": "",
    "scan": {
        "time-created": "2017-07-14 10:56:19",
        "mock-config": "rhel-7-x86_64"
    }
}
"""

ADDED_FAIL = """
{
    "defects": "some, defects",
    "scan": {
        "time-created": "2017-07-14 10:56:19",
        "mock-config": "rhel-7-x86_64"
    }
}
"""

FIXED = """
{
    "defects": "",
    "scan": {
        "time-created": "2017-07-27 14:08:11",
        "mock-config": "rhel-7-x86_64-basescan"
    }
}
"""


@pytest.fixture(name='module')
def fixture_module():
    return create_module(CICovscan)


def test_no_brew(module, monkeypatch):

    def mocked_shared(key):
        return {
            'brew_task': None
        }[key]

    _, module = module

    monkeypatch.setattr(module, 'shared', mocked_shared)

    with pytest.raises(libci.CIError, match=r'^no brew build found, did you run brew module?'):
        module.execute()


def test_blacklisted_component(module, monkeypatch):
    component_name = 'kernel'
    blacklist = component_name + ',libreoffice'

    mocked_task = MagicMock(component=component_name)
    mocked_info = MagicMock()

    def mocked_shared(key):
        return {
            'brew_task': mocked_task
        }[key]

    def mocked_option(key):
        return {
            'blacklist': blacklist
        }[key]

    _, module = module

    monkeypatch.setattr(module, 'shared', mocked_shared)
    monkeypatch.setattr(module, 'option', mocked_option)
    monkeypatch.setattr(module, 'info', mocked_info)

    module.execute()

    mocked_info.assert_called_with('Skipping blacklisted package {}'.format(component_name))


def test_not_enabled_target(module, monkeypatch):
    enabled_target = '(rhel|RHEL)-[67].[0-9]+(-z)?-candidate,rhel-7.1-ppc64le(-z)?-candidate'
    component_name = 'ssh'
    target = 'not_allowed'

    mocked_target = MagicMock(target=target)
    mocked_task = MagicMock(target=mocked_target, component=component_name)
    mocked_info = MagicMock()
    mocked_scan = MagicMock()

    def mocked_shared(key):
        return {
            'brew_task': mocked_task
        }[key]

    def mocked_option(key):
        return {
            'blacklist': None,
            'target_pattern': enabled_target
        }[key]

    _, module = module

    monkeypatch.setattr(module, 'shared', mocked_shared)
    monkeypatch.setattr(module, 'option', mocked_option)
    monkeypatch.setattr(module, 'info', mocked_info)
    monkeypatch.setattr(module, 'scan', mocked_scan)

    module.execute()

    mocked_info.assert_called_with('Target {} is not enabled, skipping job'.format(target))


def run(result, module, monkeypatch, tmpdir):
    enabled_target = '(rhel|RHEL)-[67].[0-9]+(-z)?-candidate,rhel-7.1-ppc64le(-z)?-candidate'
    component_name = 'ssh'
    target = 'rhel-7.4-candidate'

    mocked_target = MagicMock(target=target)
    mocked_task = MagicMock(target=mocked_target, component=component_name)
    mocked_info = MagicMock()

    def mocked_urlopen(url):
        if (url.find('added') or url.find('fixed')) != -1:
            file_name = 'dummy_file.html'
            outfile = tmpdir.join(file_name)

            if result == 'PASSED':
                outfile.write(ADDED_PASS)
            elif result == 'FAILED':
                outfile.write(ADDED_FAIL)
            return outfile

        elif (url.find('stdout')) != -1:
            file_name = str(tmpdir) + 'dummy_file.log.gz'
            content = b'Lots of content here'
            with gzip.open(file_name, 'wb') as outfile:
                outfile.write(content)
            return open(file_name, 'r')

        else:
            return ''

    def mocked_run_command(cmd):
        with open(cmd[-1], 'w') as outfile:
            outfile.write('1234')

    def mocked_grabber(cmd):
        if cmd.find('rpm') != -1:
            file_name = 'dummy_file.rpm'
            with open(file_name, 'w') as outfile:
                outfile.write('')
            return os.path.abspath(file_name)
        else:
            pass

    def mocked_shared(key):
        return {
            'brew_task': mocked_task
        }[key]

    def mocked_option(key):
        return {
            'blacklist': None,
            'target_pattern': enabled_target,
            'task-id': None
        }[key]

    _, module = module

    monkeypatch.setattr(module, 'shared', mocked_shared)
    monkeypatch.setattr(module, 'option', mocked_option)
    monkeypatch.setattr(module, 'info', mocked_info)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlgrab', mocked_grabber)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'run_command', mocked_run_command)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)

    module.execute()

    mocked_info.assert_called_with('Result of testing: {}'.format(result))


def test_pass_run(module, monkeypatch, tmpdir):
    run('PASSED', module, monkeypatch, tmpdir)


def test_fail_run(module, monkeypatch, tmpdir):
    run('FAILED', module, monkeypatch, tmpdir)


def test_run_command_error(module, monkeypatch):
    _, module = module

    output = MagicMock(exit_code=1)

    def mocked_run_command(cmd):
        raise libci.CICommandError(cmd, output)

    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'run_command', mocked_run_command)

    with pytest.raises(libci.CIError, match=r"^Failure during 'covscan' execution"):
        module.version_diff_build('srpm', 'baseline', 'config', 'baseconfig')


def test_invalid_json(monkeypatch, tmpdir):
    def mocked_urlopen(url):
        # pylint: disable=unused-argument
        file_name = 'dummy_file.html'
        outfile = tmpdir.join(file_name)
        outfile.write('{{{ some invalid json')
        return outfile

    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)

    result = CovscanResult(MagicMock(brew_task=111), 000)

    with pytest.raises(CovscanFailedError):
        # pylint: disable=pointless-statement
        result.added


def test_no_baseline(module, monkeypatch):
    _, module = module

    def mocked_option(key):
        return {
            'task-id': None
        }[key]

    monkeypatch.setattr(module, 'option', mocked_option)

    module.brew_task = MagicMock(latest=None)

    with pytest.raises(NoCovscanBaselineFoundError):
        module.scan()


def test_fetch_added(monkeypatch, tmpdir):
    def mocked_urlopen(url):
        # pylint: disable=unused-argument
        file_name = 'dummy_file.html'
        outfile = tmpdir.join(file_name)

        outfile.write(ADDED_PASS)
        return outfile

    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)
    result = CovscanResult(MagicMock(brew_task=111), 000)

    assert result.added == ''


def test_fetch_fixed(monkeypatch, tmpdir):
    def mocked_urlopen(url):
        # pylint: disable=unused-argument
        file_name = 'dummy_file.html'
        outfile = tmpdir.join(file_name)
        outfile.write(FIXED)
        return outfile

    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)
    result = CovscanResult(MagicMock(brew_task=111), 000)

    assert result.fixed == ''


def test_covscan_fail(module, monkeypatch, tmpdir):
    _, module = module

    def mocked_option(key):
        return {
            'task-id': None
        }[key]

    def mocked_grabber(cmd):
        if cmd.find('rpm') != -1:
            file_name = 'dummy_file.rpm'
            with open(file_name, 'w') as outfile:
                outfile.write('')
            return os.path.abspath(file_name)
        else:
            pass

    def mocked_run_command(cmd):
        with open(cmd[-1], 'w') as outfile:
            outfile.write('1234')

    def mocked_urlopen(url):
        # pylint: disable=unused-argument
        file_name = str(tmpdir) + 'dummy_file.log.gz'
        content = b"Failing because of at least one subtask hasn't closed properly.\n"
        with gzip.open(file_name, 'wb') as outfile:
            outfile.write(content)

        return open(file_name, 'r')

    monkeypatch.setattr(module, 'option', mocked_option)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlgrab', mocked_grabber)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'run_command', mocked_run_command)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)

    mocked_target = MagicMock(destination_tag='destination_tag', rhel='rhel')
    module.brew_task = MagicMock(latest='baseline', target=mocked_target, srpm='srpm')

    with pytest.raises(CovscanFailedError):
        module.scan()


def test_setted_taskid(module, monkeypatch, tmpdir):
    _, module = module

    def mocked_option(key):
        return {
            'task-id': '1234'
        }[key]

    def mocked_urlopen(url):
        if (url.find('added') or url.find('fixed')) != -1:
            file_name = 'dummy_file.html'
            outfile = tmpdir.join(file_name)
            outfile.write(ADDED_PASS)
            return outfile

        elif (url.find('stdout')) != -1:
            file_name = str(tmpdir) + 'dummy_file.log.gz'
            content = b'Lots of content here'
            with gzip.open(file_name, 'wb') as outfile:
                outfile.write(content)
            return open(file_name, 'r')

    def mocked_grabber(cmd):
        # pylint: disable=unused-argument
        pass

    mocked_info = MagicMock()

    monkeypatch.setattr(module, 'option', mocked_option)
    monkeypatch.setattr(module, 'info', mocked_info)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlgrab', mocked_grabber)

    module.scan()

    mocked_info.assert_any_call('Skipping covscan testing, using existing Covscan task id')
