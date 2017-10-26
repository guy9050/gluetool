# pylint: disable=protected-access
import os
import urllib2
import pytest
from mock import MagicMock
import jenkinsapi.jenkins

import libci.proxy
from libci.modules.infrastructure.jenkins import CIJenkins, JenkinsProxy
from . import create_module

CONFIG_FILE_PATTERN = """[jenkins]
url = dummy_url
user = dummy_username
password = dummy_passwd

"""


@pytest.fixture(name='module')
def fixture_module():
    return create_module(CIJenkins)


def test_loadable(module):
    ci, _ = module
    python_mod = ci._load_python_module('helpers/jenkins', 'pytest_jenkins',
                                        'libci/modules/infrastructure/jenkins.py')

    assert hasattr(python_mod, 'CIJenkins')


def test_execute(module, monkeypatch, tmpdir):
    _, module = module

    module._config['create-jjb-config'] = True
    module._config['url'] = 'dummy_url'
    module._config['password'] = 'dummy_passwd'
    module._config['username'] = 'dummy_username'

    path_to_config = str(tmpdir.join('jenkins_jobs.ini'))
    monkeypatch.setattr(libci.modules.infrastructure.jenkins, 'JJB_CONFIG', path_to_config)

    monkeypatch.setattr(os.path, 'exists', MagicMock(return_value=False))
    monkeypatch.setattr(os, 'makedirs', MagicMock())

    monkeypatch.setattr(jenkinsapi.jenkins.Jenkins, '__init__', MagicMock(return_value=None))
    monkeypatch.setattr(jenkinsapi.jenkins.Jenkins, '__getattribute__', MagicMock(return_value=None))
    monkeypatch.setattr(libci.proxy.Proxy, '__new__', MagicMock(return_value=MagicMock()))

    module.execute()

    with open(path_to_config, 'r') as config_file:
        config_file_content = config_file.read()

    assert config_file_content == CONFIG_FILE_PATTERN


def test_connection_error(module):
    _, module = module

    module._config['create-jjb-config'] = True
    module._config['url'] = 'dummy_url'
    module._config['password'] = 'dummy_passwd'
    module._config['username'] = 'dummy_username'

    with pytest.raises(libci.CIError, match=r'^could not connect to jenkins'):
        module.connect()


def test_shared_jenkins(module, monkeypatch):
    _, module = module
    mocked_jenkins = MagicMock()
    fake_mocked_jenkins = MagicMock()

    module._jenkins = fake_mocked_jenkins

    monkeypatch.setattr(jenkinsapi.jenkins.Jenkins, '__init__', MagicMock(return_value=None))
    monkeypatch.setattr(jenkinsapi.jenkins.Jenkins, '__getattribute__', MagicMock(return_value=None))
    monkeypatch.setattr(libci.proxy.Proxy, '__new__', MagicMock(return_value=mocked_jenkins))

    assert module.jenkins(reconnect=True) == mocked_jenkins
    assert not module.jenkins(reconnect=True) == fake_mocked_jenkins


def test_jenkins_set_build_name(log, module, monkeypatch):
    _, module = module
    jenkins_proxy = JenkinsProxy(MagicMock(), module)

    mocked_urlopen = MagicMock()
    mocked_urlopen.getcode.return_value = 200

    monkeypatch.setattr(urllib2, 'urlopen', MagicMock(return_value=mocked_urlopen))

    monkeypatch.setenv('BUILD_URL', '')
    module._config['url'] = 'dummy_jenkins_url'

    build_name = 'dummy_name'
    jenkins_proxy.set_build_name(build_name)
    assert log.records[-1].message == "build name set:\n  name='{}'\n  description=''".format(build_name)


def test_jenkins_set_build_name_credentials(log, module, monkeypatch):
    _, module = module

    module._config['username'] = 'dummy_username'
    module._config['password'] = 'dummy_password'

    jenkins_proxy = JenkinsProxy(MagicMock(), module)

    mocked_urlopen = MagicMock()
    mocked_urlopen.getcode.return_value = 200

    monkeypatch.setattr(urllib2, 'urlopen', MagicMock(return_value=mocked_urlopen))

    monkeypatch.setenv('BUILD_URL', '')
    module._config['url'] = 'dummy_jenkins_url'

    build_name = 'dummy_name'
    jenkins_proxy.set_build_name(build_name)
    assert log.records[-1].message == "build name set:\n  name='{}'\n  description=''".format(build_name)


def test_jenkins_set_build_name_url_not_found(module, monkeypatch):
    _, module = module
    jenkins_proxy = JenkinsProxy(MagicMock(), module)

    mocked_urlopen = MagicMock()
    mocked_urlopen.getcode.return_value = 404

    monkeypatch.setattr(urllib2, 'urlopen', MagicMock(return_value=mocked_urlopen))

    monkeypatch.setenv('BUILD_URL', '')
    module._config['url'] = 'dummy_jenkins_url'

    with pytest.raises(libci.CIError, match=r'^Jenkins REST request failed'):
        jenkins_proxy.set_build_name('dummy_name')


def test_jenkins_set_build_name_no_build_url(module, monkeypatch):
    _, module = module
    jenkins_proxy = JenkinsProxy(MagicMock(), module)

    try:
        monkeypatch.delenv('BUILD_URL')
    except KeyError:
        pass

    with pytest.raises(libci.CIError, match=r'^\$BUILD_URL env var not found, was this job started by Jenkins?'):
        jenkins_proxy.set_build_name('dummy_name')


def test_jenkins_set_build_name_cross_site(module, monkeypatch):
    _, module = module
    jenkins_proxy = JenkinsProxy(MagicMock(), module)

    monkeypatch.setenv('BUILD_URL', 'dummy_jenkins_url')
    module._config['url'] = 'fake_jenkins_url'

    with pytest.raises(libci.CIError, match=r'^Cross-site Jenkins REST request'):
        jenkins_proxy.set_build_name('dummy_name')
