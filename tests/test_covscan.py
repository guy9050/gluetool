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


def test_loadable(module):
    ci, _ = module
    # pylint: disable=protected-access
    python_mod = ci._load_python_module('static_analysis/covscan', 'pytest_covscan',
                                        'libci/modules/static_analysis/covscan/covscan.py')

    assert hasattr(python_mod, 'CICovscan')


def test_no_brew(module, monkeypatch):
    _, module = module

    monkeypatch.setattr(module, 'shared', MagicMock(return_value=None))

    with pytest.raises(libci.CIError, match=r'^no brew build found, did you run brew module?'):
        module.execute()


def test_blacklisted_component(log, module, monkeypatch):
    component_name = 'kernel'

    _, module = module
    module._config['blacklist'] = '{},libreoffice'.format(component_name)

    monkeypatch.setattr(module, 'shared', MagicMock(return_value=MagicMock(component=component_name)))

    module.execute()

    assert log.records[-1].message == 'Skipping blacklisted package {}'.format(component_name)


def test_not_enabled_target(log, module, monkeypatch):
    enabled_target = '(rhel|RHEL)-[67].[0-9]+(-z)?-candidate,rhel-7.1-ppc64le(-z)?-candidate'
    component_name = 'ssh'
    target = 'not_allowed'

    mocked_task = MagicMock(target=MagicMock(target=target), component=component_name)

    _, module = module
    module._config['target_pattern'] = enabled_target

    monkeypatch.setattr(module, 'shared', MagicMock(return_value=mocked_task))

    module.execute()

    assert log.records[-1].message == 'Target {} is not enabled, skipping job'.format(target)


def run(result, log, module, monkeypatch, tmpdir):
    enabled_target = '(rhel|RHEL)-[67].[0-9]+(-z)?-candidate,rhel-7.1-ppc64le(-z)?-candidate'
    component_name = 'ssh'
    target = 'rhel-7.4-candidate'

    mocked_target = MagicMock(target=target)
    mocked_task = MagicMock(target=mocked_target, component=component_name, srcrpm='dummy.src.rpm')

    _, module = module
    module._config['target_pattern'] = enabled_target

    def mocked_urlopen(url):
        if 'added' in url or 'fixed' in url:
            file_name = 'dummy_file.html'
            outfile = tmpdir.join(file_name)

            if result == 'PASSED':
                outfile.write(ADDED_PASS)
            elif result == 'FAILED':
                outfile.write(ADDED_FAIL)
            return outfile

        elif 'stdout' in url:
            outfile = tmpdir.join('dummy_file.log.gz')
            with gzip.open(str(outfile), 'wb') as f:
                f.write(b'Lots of content here')
            return outfile

        else:
            return ''

    def mocked_run_command(cmd):
        with open(cmd[-1], 'w') as outfile:
            outfile.write('1234')

    def mocked_grabber(url):
        if 'rpm' in url:
            with open(url, 'w') as outfile:
                outfile.write('')
            return os.path.abspath(url)
        else:
            pass

    def mocked_shared(key):
        return {
            'task': mocked_task
        }[key]

    monkeypatch.setattr(module, 'shared', mocked_shared)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlgrab', mocked_grabber)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'run_command', mocked_run_command)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)

    module.execute()

    assert log.records[-2].message == 'Result of testing: {}'.format(result)


def test_pass_run(log, module, monkeypatch, tmpdir):
    run('PASSED', log, module, monkeypatch, tmpdir)


def test_fail_run(log, module, monkeypatch, tmpdir):
    run('FAILED', log, module, monkeypatch, tmpdir)


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


def test_no_baseline(module):
    _, module = module

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

    def mocked_grabber(cmd):
        # pylint: disable=unused-argument
        file_name = 'dummy_file.rpm'
        with open(file_name, 'w') as outfile:
            outfile.write('')
        return os.path.abspath(file_name)

    def mocked_run_command(cmd):
        with open(cmd[-1], 'w') as outfile:
            outfile.write('1234')

    def mocked_urlopen(url):
        # pylint: disable=unused-argument
        outfile = tmpdir.join('dummy_file.log.gz')
        with gzip.open(str(outfile), 'wb') as f:
            f.write(b"Failing because of at least one subtask hasn't closed properly.\n")
        return outfile

    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlgrab', mocked_grabber)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'run_command', mocked_run_command)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)

    mocked_target = MagicMock(destination_tag='destination_tag', rhel='rhel')
    module.brew_task = MagicMock(latest='baseline', target=mocked_target, srpm='srpm')

    with pytest.raises(CovscanFailedError):
        module.scan()


def test_setted_taskid(log, module, monkeypatch, tmpdir):
    _, module = module
    module._config['task-id'] = '1234'

    def mocked_urlopen(url):
        if 'added' in url or 'fixed' in url:
            file_name = 'dummy_file.html'
            outfile = tmpdir.join(file_name)
            outfile.write(ADDED_PASS)
            return outfile

        elif 'stdout' in url:
            outfile = tmpdir.join('dummy_file.log.gz')
            with gzip.open(str(outfile), 'wb') as f:
                f.write(b'Lots of content here')
            return outfile

    def mocked_grabber(cmd):
        # pylint: disable=unused-argument
        pass

    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlgrab', mocked_grabber)

    module.scan()

    message = 'Skipping covscan testing, using existing Covscan task id'
    assert any(record.message == message for record in log.records)
