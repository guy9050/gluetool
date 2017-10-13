import pytest

import libci
import libci.dispatch_job

from . import create_module, patch_shared


class FakeJenkins(object):
    # pylint: disable=too-few-public-methods

    def __init__(self, **expected_build_params):
        self.expected_build_params = expected_build_params

    def invoke(self, build_params=None):
        assert self.expected_build_params == build_params


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    class DummyDispatchModule(libci.dispatch_job.DispatchJenkinsJobMixin, libci.Module):
        name = 'dummy-job'

    return create_module(DummyDispatchModule)


def create_build_params(mod, **kwargs):
    """
    Initialize module configuration using provided options and their values.
    """

    params = {
        'testing_thread_id': 'deadbeef',
        'id': 17,
        'pipeline_prepend': 'some prepended options',
        'pipeline_append': 'some appended options',
        'pipeline_state_reporter_options': 'some pipeline-report options',
        'notify_recipients_options': ['more', 'and', 'more', 'recipients'],
        'notify_email_options': 'email options'
    }

    params.update(kwargs)

    for name, value in params.iteritems():
        # pylint: disable=protected-access
        mod._config[name.replace('_', '-')] = value

    # notify-recipients-options will be presented as a string
    if not params['notify_recipients_options']:
        params['notify_recipients_options'] = None
    else:
        params['notify_recipients_options'] = ' '.join([str(s) for s in params['notify_recipients_options']])

    return params


def test_sanity(module):
    # pylint: disable=unused-argument
    pass


def test_no_task_id(module):
    _, mod = module

    with pytest.raises(libci.CIError, match=r'Task ID not specified'):
        mod.sanity()


def test_task_id(module):
    _, mod = module

    # pylint: disable=protected-access
    mod._config['id'] = 17

    mod.sanity()


def test_task_id_from_env(module, monkeypatch):
    _, mod = module

    monkeypatch.setenv('id', '17')

    # pylint: disable=protected-access
    mod._config['id'] = 13

    mod.sanity()

    # pylint: disable=protected-access
    assert int(mod._config['id']) == 17


def test_no_jenkins(module):
    _, mod = module

    # pylint: disable=protected-access
    mod._config['id'] = 13

    with pytest.raises(libci.CIError,
                       match=r"Module requires Jenkins connection, provided e\.g\. by the 'jenkins' module"):
        mod.execute()


def test_build_params(module):
    _, mod = module

    expected_params = create_build_params(mod)

    assert mod.build_params == expected_params


def test_no_recipients(module):
    _, mod = module

    expected_params = create_build_params(mod, notify_recipients_options=None)

    assert mod.build_params == expected_params


def test_dispatch(module, monkeypatch, job_name='ci-dummy'):
    ci, mod = module

    # Init options & build params
    expected_params = create_build_params(mod)

    # DispatchJenkinsJobModule does not have any build byt default, let's set use some dummy name
    mod.job_name = job_name

    patch_shared(monkeypatch, mod, {
        'jenkins': {
            job_name: FakeJenkins(**expected_params)
        }
    })

    mod.execute()
