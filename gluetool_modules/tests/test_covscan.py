import os
import pytest

from mock import MagicMock
import gluetool
import gluetool.utils
from gluetool.glue import DryRunLevels
import gluetool_modules.static_analysis.covscan.covscan
from gluetool_modules.static_analysis.covscan.covscan import CICovscan, CovscanResult, \
    CovscanFailedError, NoCovscanBaselineFoundError
from . import create_module, patch_shared, assert_shared, testing_asset, check_loadable

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
    glue, _ = module

    check_loadable(glue, 'gluetool_modules/static_analysis/covscan/covscan.py', 'CICovscan')


def test_no_brew(module):
    _, module = module

    assert_shared('primary_task', module.execute)


def test_blacklisted_component(log, module, monkeypatch):
    component_name = 'kernel'

    _, module = module
    module._config['blacklist'] = '{},libreoffice'.format(component_name)

    patch_shared(monkeypatch, module, {}, callables={
        'primary_task': MagicMock(return_value=MagicMock(component=component_name))
    })

    module.execute()

    assert log.records[-1].message == 'Package {} is blacklisted, skipping job'.format(component_name)


def test_not_enabled_target(log, module, monkeypatch):
    enabled_target = '(rhel|RHEL)-[67].[0-9]+(-z)?-candidate,rhel-7.1-ppc64le(-z)?-candidate'
    component_name = 'ssh'
    target = 'not_allowed'

    _, module = module
    module._config['target_pattern'] = enabled_target

    patch_shared(monkeypatch, module, {}, callables={
        'primary_task': MagicMock(return_value=MagicMock(target=target, component=component_name))
        })

    module.execute()

    assert log.records[-1].message == 'Target {} is not enabled, skipping job'.format(target)


def run(result, log, module, monkeypatch, tmpdir):
    enabled_target = '(rhel|RHEL)-[67].[0-9]+(-z)?-candidate,rhel-7.1-ppc64le(-z)?-candidate'
    component_name = 'ssh'
    target = 'rhel-7.4-candidate'

    baseline_config = testing_asset('covscan', 'example-config-map.yml')

    mocked_baseline = MagicMock(target=target, component=component_name, srpm_urls=['dummy_baseline.src.rpm'])
    mocked_task = MagicMock(target=target,
                            component=component_name,
                            srpm_urls=['dummy_target.src.rpm'],
                            latest_released=MagicMock(return_value=mocked_baseline))

    # _, module = module
    module._config['target_pattern'] = enabled_target

    module._config.update({
        'target_pattern': enabled_target,
        'config-map': str(baseline_config),
        'covscan-task-url-template': 'https://cov01.lab.eng.brq.redhat.com/covscanhub/task/{{ COVSCAN_TASK_ID }}/'
    })

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

    class MockedCommand(object):

        def __init__(self, command, *args, **kwargs):
            self.cmd = command

        def run(self):
            if self.cmd[1] == 'version-diff-build':
                with open(self.cmd[-1], 'w') as outfile:
                    outfile.write('1234')
            elif self.cmd[1] == 'task-info':
                if result in ['PASSED', 'FAILED']:
                    return MagicMock(stdout=TASK_INFO_PASS)
                if result in ['FAIL']:
                    return MagicMock(stdout=TASK_INFO_FAIL)

    def mocked_grabber(url, filename=None):
        if 'rpm' in url:
            with open(filename or url, 'w') as outfile:
                outfile.write('')
            return os.path.abspath(filename or url)
        else:
            pass

    patch_shared(monkeypatch, module, {
        'primary_task': mocked_task
    })

    monkeypatch.setattr(gluetool_modules.static_analysis.covscan.covscan, 'Command', MockedCommand)
    monkeypatch.setattr(gluetool_modules.static_analysis.covscan.covscan, 'urlgrab', mocked_grabber)
    monkeypatch.setattr(gluetool_modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)

    module.execute()


def test_pass_run(log, module, monkeypatch, tmpdir):
    _, module = module
    result = 'PASSED'
    run(result, log, module, monkeypatch, tmpdir)
    assert log.match(message='Result of testing: {}'.format(result))


def test_fail_run(log, module, monkeypatch, tmpdir):
    _, module = module
    result = 'FAILED'
    run(result, log, module, monkeypatch, tmpdir)
    assert log.match(message='Result of testing: {}'.format(result))


def test_run_command_error(module, monkeypatch):
    _, module = module

    output = MagicMock(exit_code=1)

    def mocked_run_command(cmd):
        raise gluetool.GlueCommandError(cmd, output)

    monkeypatch.setattr(gluetool_modules.static_analysis.covscan.covscan.Command, 'run', mocked_run_command)

    with pytest.raises(gluetool.GlueError, match=r"^Failure during 'covscan' execution"):
        module.version_diff_build('srpm', 'baseline', 'config', 'baseconfig')


def test_invalid_json(monkeypatch, tmpdir):
    def mocked_urlopen(url):
        file_name = 'dummy_file.html'
        outfile = tmpdir.join(file_name)
        outfile.write('{{{ some invalid json')
        return outfile

    monkeypatch.setattr(gluetool_modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)

    module = MagicMock(task=111)
    module.option = MagicMock(
        return_value='https://cov01.lab.eng.brq.redhat.com/covscanhub/task/{{ COVSCAN_TASK_ID }}/')
    result = CovscanResult(module, 000)

    with pytest.raises(CovscanFailedError):
        result.added


def test_no_baseline(module):
    _, module = module

    module.task = MagicMock(latest_released=MagicMock(return_value=None))

    with pytest.raises(NoCovscanBaselineFoundError):
        module.scan()


def test_fetch_added(monkeypatch, tmpdir):
    def mocked_urlopen(url):
        file_name = 'dummy_file.html'
        outfile = tmpdir.join(file_name)

        outfile.write(ADDED_PASS)
        return outfile

    monkeypatch.setattr(gluetool_modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)

    module = MagicMock(task=111)
    module.option = MagicMock(
        return_value='https://cov01.lab.eng.brq.redhat.com/covscanhub/task/{{ COVSCAN_TASK_ID }}/')
    result = CovscanResult(module, 000)

    assert result.added == ''


def test_fetch_fixed(monkeypatch, tmpdir):
    def mocked_urlopen(url):
        file_name = 'dummy_file.html'
        outfile = tmpdir.join(file_name)
        outfile.write(FIXED)
        return outfile

    monkeypatch.setattr(gluetool_modules.static_analysis.covscan.covscan, 'urlopen', mocked_urlopen)

    module = MagicMock(task=111)
    module.option = MagicMock(
        return_value='https://cov01.lab.eng.brq.redhat.com/covscanhub/task/{{ COVSCAN_TASK_ID }}/')
    result = CovscanResult(module, 000)

    assert result.fixed == ''


def test_covscan_fail(log, module, monkeypatch, tmpdir):
    _, module = module

    with pytest.raises(CovscanFailedError):
        run('FAIL', log, module, monkeypatch, tmpdir)


def test_only_task_id(log, module, monkeypatch, tmpdir):
    _, module = module
    module._config['task-id'] = '1234'

    run('PASSED', log, module, monkeypatch, tmpdir)
    assert log.match(message='Skipping covscan testing, using existing Covscan task id 1234')


def test_dry_run_with_task_id(log, module, monkeypatch, tmpdir):
    ci, module = module
    ci._dryrun_level = DryRunLevels.DRY
    module._config['task-id'] = '1234'

    run('PASSED', log, module, monkeypatch, tmpdir)
    assert log.match(message='Skipping covscan testing, using existing Covscan task id 1234')


def test_dry_run_without_taskid(module):
    ci, module = module
    ci._dryrun_level = DryRunLevels.DRY

    with pytest.raises(gluetool.GlueError, match=r"^Can not run covscan dryrun without task-id parameter"):
        module.scan()
