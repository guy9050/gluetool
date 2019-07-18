import pytest
from mock import MagicMock
from gluetool_modules.helpers import beaker_jobwatch
from gluetool import GlueCommandError
from . import create_module, check_loadable


USUAL_JOBWATCH_LOG = """Broken: 0
Running:   0/1
Completed: 1/1
TJ#1739067
https://beaker.engineering.redhat.com/matrix/?toggle_nacks_on=on&job_ids=1739067
duration: 3:39:03.805050
finished successfully"""

SHORT_JOBWATCH_LOG = """Broken: 0
finished successfully"""

NO_MATRIX_JOBWATCH_LOG = """Broken: 0
Running:   0/1
Completed: 1/1
TJ#1739067
duration: 3:39:03.805050
finished successfully"""

NO_COMPLETION_JOBWATCH_LOG = """Broken: 0
Running:   0/1
Completed: 1/1
TJ#1739067
https://beaker.engineering.redhat.com/matrix/?toggle_nacks_on=on&job_ids=1739067
duration: 3:39:03.805050"""


@pytest.fixture(name='module')
def fixture_module():
    module = create_module(beaker_jobwatch.BeakerJobwatch)[1]
    module._config['matrix-baseurl'] = 'https://beaker.engineering.redhat.com/matrix/'
    module._config['jobwatch-options'] = '--max=10'
    return module


def test_loadable(module):
    check_loadable(module.glue, 'gluetool_modules/helpers/beaker_jobwatch.py', 'BeakerJobwatch')


def test_get_matrix_url(module):
    assert module._get_matrix_url(
        USUAL_JOBWATCH_LOG) == 'https://beaker.engineering.redhat.com/matrix/?toggle_nacks_on=on&job_ids=1739067'


def test_get_matrix_url_short(module):
    with pytest.raises(beaker_jobwatch.BeakerJobwatchError, match='jobwatch output is unexpectedly short'):
        module._get_matrix_url(SHORT_JOBWATCH_LOG)


def test_get_matrix_url_no_matrix(module):
    with pytest.raises(beaker_jobwatch.BeakerJobwatchError, match='Could not find beaker matrix URL in jobwatch output'):
        module._get_matrix_url(NO_MATRIX_JOBWATCH_LOG)


def test_get_matrix_url_no_completion(module):
    with pytest.raises(beaker_jobwatch.BeakerJobwatchError, match='beaker-jobwatch does not report completion'):
        module._get_matrix_url(NO_COMPLETION_JOBWATCH_LOG)


def test_beaker_jobwatch(module, monkeypatch):
    cmd_mock = MagicMock(options=[], run=MagicMock(return_value=MagicMock(stdout=USUAL_JOBWATCH_LOG)))
    command_mock = MagicMock(return_value=cmd_mock)
    monkeypatch.setattr(beaker_jobwatch.gluetool.utils, 'Command', command_mock)

    output, matrix_url = module.beaker_jobwatch(['dummy-job1', 'dummy-job2'], 'dummy-end_task', ['dummy-critical_task'])

    assert cmd_mock.run.return_value == output
    assert matrix_url == 'https://beaker.engineering.redhat.com/matrix/?toggle_nacks_on=on&job_ids=1739067'
    assert cmd_mock.options == ['--max=10',
                                '--job', 'dummy-job1',
                                '--job', 'dummy-job2',
                                '--end-task', 'dummy-end_task',
                                '--critical-task', 'dummy-critical_task']


def test_beaker_jobwatch_error(module, monkeypatch):
    mock_error = GlueCommandError([], output=MagicMock(exit_code=2, stdout=USUAL_JOBWATCH_LOG))
    cmd_mock = MagicMock(return_value=MagicMock(options=[], run=MagicMock(side_effect=mock_error)))
    monkeypatch.setattr(beaker_jobwatch.gluetool.utils, 'Command', cmd_mock)

    with pytest.raises(beaker_jobwatch.BeakerJobwatchAbortedError, match='Beaker job\(s\) aborted, inform the user nicely'):
        module.beaker_jobwatch(['dummy-job1', 'dummy-job2'])


def test_beaker_jobwatch_fail(module, monkeypatch):
    mock_error = GlueCommandError([], output=MagicMock(stdout=USUAL_JOBWATCH_LOG))
    cmd_mock = MagicMock(return_value=MagicMock(options=[], run=MagicMock(side_effect=mock_error)))
    monkeypatch.setattr(beaker_jobwatch.gluetool.utils, 'Command', cmd_mock)

    with pytest.raises(beaker_jobwatch.BeakerJobwatchError, match="Failure during 'jobwatch' execution"):
        module.beaker_jobwatch(['dummy-job1', 'dummy-job2'])
