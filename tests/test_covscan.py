# pylint: disable=protected-access
import os
import pytest

from mock import MagicMock
import libci
import libci.utils
from libci.ci import DryRunLevels
from libci.modules.static_analysis.covscan.covscan import CICovscan, CovscanResult, \
    CovscanFailedError, NoCovscanBaselineFoundError
from . import create_module, patch_shared, assert_shared

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

TASK_INFO_PASS = """
exclusive = False
resubmitted_by = None
weight = 1
state_label = CLOSED
awaited = False
result =
owner = jenkins/baseos-jenkins.rhev-ci-vms.eng.rdu2.redhat.com
id = 55188
state = 3
label = netpbm-10.79.00-1.el7.src.rpm
priority = 10
waiting = False
method = VersionDiffBuild
channel = 1
parent = None
"""

TASK_INFO_FAIL = """
exclusive = False
resubmitted_by = None
weight = 1
state_label = FAILED
awaited = False
result =
owner = jenkins/baseos-jenkins.rhev-ci-vms.eng.rdu2.redhat.com
id = 55122
state = 5
label = qt5-qtimageformats-5.9.1-1.el7.src.rpm
priority = 10
waiting = False
method = VersionDiffBuild
channel = 1
parent = None
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


def test_no_brew(module):
    _, module = module

    assert_shared('primary_task', module.execute)


def test_blacklisted_component(log, module, monkeypatch):
    component_name = 'kernel'

    _, module = module
    module._config['blacklist'] = '{},libreoffice'.format(component_name)

    monkeypatch.setattr(module.ci, 'shared_functions', {
        'primary_task': (None, MagicMock(return_value=MagicMock(component=component_name)))
    })

    module.execute()

    assert log.records[-1].message == 'Package {} is blacklisted, skipping job'.format(component_name)


def test_not_enabled_target(log, module, monkeypatch):
    enabled_target = '(rhel|RHEL)-[67].[0-9]+(-z)?-candidate,rhel-7.1-ppc64le(-z)?-candidate'
    component_name = 'ssh'
    target = 'not_allowed'

    _, module = module
    module._config['target_pattern'] = enabled_target

    monkeypatch.setattr(module.ci, 'shared_functions', {
        'primary_task': (None, MagicMock(return_value=MagicMock(target=target, component=component_name)))
    })

    module.execute()

    assert log.records[-1].message == 'Target {} is not enabled, skipping job'.format(target)


def run(result, log, module, monkeypatch, tmpdir):
    enabled_target = '(rhel|RHEL)-[67].[0-9]+(-z)?-candidate,rhel-7.1-ppc64le(-z)?-candidate'
    component_name = 'ssh'
    target = 'rhel-7.4-candidate'

    mocked_task = MagicMock(target=target, component=component_name, srcrpm='dummy.src.rpm')

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
        else:
            return ''

    def mocked_run_command(cmd):
        if cmd[1] == 'version-diff-build':
            with open(cmd[-1], 'w') as outfile:
                outfile.write('1234')
        elif cmd[1] == 'task-info':
            return MagicMock(stdout=TASK_INFO_PASS)

    def mocked_grabber(url):
        if 'rpm' in url:
            with open(url, 'w') as outfile:
                outfile.write('')
            return os.path.abspath(url)
        else:
            pass

    patch_shared(monkeypatch, module, {
        'primary_task': mocked_task
    })

    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlgrab', mocked_grabber)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'run_command', mocked_run_command)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)

    module.execute()

    assert log.match(message='Result of testing: {}'.format(result))


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

    result = CovscanResult(MagicMock(task=111), 000)

    with pytest.raises(CovscanFailedError):
        # pylint: disable=pointless-statement
        result.added


def test_no_baseline(module):
    _, module = module

    module.task = MagicMock(latest=None)

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
    result = CovscanResult(MagicMock(task=111), 000)

    assert result.added == ''


def test_fetch_fixed(monkeypatch, tmpdir):
    def mocked_urlopen(url):
        # pylint: disable=unused-argument
        file_name = 'dummy_file.html'
        outfile = tmpdir.join(file_name)
        outfile.write(FIXED)
        return outfile

    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)
    result = CovscanResult(MagicMock(task=111), 000)

    assert result.fixed == ''


def test_covscan_fail(module, monkeypatch):
    _, module = module

    def mocked_grabber(cmd):
        # pylint: disable=unused-argument
        file_name = 'dummy_file.rpm'
        with open(file_name, 'w') as outfile:
            outfile.write('')
        return os.path.abspath(file_name)

    def mocked_run_command(cmd):
        if cmd[1] == 'version-diff-build':
            with open(cmd[-1], 'w') as outfile:
                outfile.write('1234')
        elif cmd[1] == 'task-info':
            return MagicMock(stdout=TASK_INFO_FAIL)

    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlgrab', mocked_grabber)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'run_command', mocked_run_command)

    module.task = MagicMock(latest='baseline', destination_tag='destiantion_tag', rhel='rhel', srpm='srpm')

    with pytest.raises(CovscanFailedError):
        module.scan()


def general_dry_run(log, module, monkeypatch, tmpdir):

    def mocked_urlopen(url):
        # pylint: disable=unused-argument
        file_name = 'dummy_file.html'
        outfile = tmpdir.join(file_name)
        outfile.write(ADDED_PASS)
        return outfile

    def mocked_grabber(cmd):
        # pylint: disable=unused-argument
        pass

    def mocked_run_command(cmd):
        if cmd[1] == 'version-diff-build':
            with open(cmd[-1], 'w') as outfile:
                outfile.write('1234')
        elif cmd[1] == 'task-info':
            return MagicMock(stdout=TASK_INFO_PASS)

    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'urlgrab', mocked_grabber)
    monkeypatch.setattr(libci.modules.static_analysis.covscan.covscan, 'run_command', mocked_run_command)

    module.scan()

    assert log.match(message='Skipping covscan testing, using existing Covscan task id 1234')


def test_only_task_id(log, module, monkeypatch, tmpdir):
    _, module = module
    module._config['task-id'] = '1234'

    general_dry_run(log, module, monkeypatch, tmpdir)


def test_dry_run_with_task_id(log, module, monkeypatch, tmpdir):
    ci, module = module
    ci._dryrun_level = DryRunLevels.DRY
    module._config['task-id'] = '1234'

    general_dry_run(log, module, monkeypatch, tmpdir)


def test_dry_run_without_taskid(module):
    ci, module = module
    ci._dryrun_level = DryRunLevels.DRY

    with pytest.raises(libci.CIError, match=r"^Can not run covscan dryrun without task-id parameter"):
        module.scan()
