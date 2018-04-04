import base64
import ConfigParser
import json
import os
import urllib
import urllib2

from jenkinsapi.jenkins import Jenkins
from requests.exceptions import RequestException

import gluetool
from gluetool import GlueError
from gluetool.log import format_dict
from gluetool.proxy import Proxy


class JenkinsProxy(Proxy):
    # pylint: disable=too-few-public-methods

    """
    Proxy wrapper of a Jenkins API instance. Instance of this class
    behaves exactly like the Jenkins API instance it wraps, user of such
    instance can use it like any other Jenkins API instance.

    To us such arrangement brings possibility to extend proxy object with
    our custom methods. That way we can provide a single object (via our
    shared function) that combines both the original behavior of Jenkins
    API instance, and our custom function we want to provide to the users
    of this module.

    When adding new methods, don't forget to update _CUSTOM_METHODS as well.

    :param CIJenkins module: our parent module.
    :param jenkinsapi.jenkins jenkins: Jenkins API connection.
    """

    _CUSTOM_METHODS = ('set_build_name', 'enable_quiet_mode', 'disable_quiet_mode')

    def __init__(self, jenkins, module):
        super(JenkinsProxy, self).__init__(jenkins)

        # This is a proxy, so 'self.foo' would change attribute of
        # the wrapped object. We don't want to mess with its attributes,
        # so we have to resort to using object's methods when we want
        # to change *this* object instead.
        object.__setattr__(self, '_module', module)

    def __getattribute__(self, name):
        """
        Original __getattribute__ method of Proxy class just forwards all
        its calls to the object Proxy wraps. To allow users use of our custom
        methods, we must "teach" our __getattribute__ about our methods.
        """

        if name in JenkinsProxy._CUSTOM_METHODS:
            return object.__getattribute__(self, name)

        return super(JenkinsProxy, self).__getattribute__(name)

    def set_build_name(self, name, description=None, build_url=None):
        """
        Set name (and possibly description) of a jenkins build.

        :param str name: desired name.
        :param str description: if not set, empty string is used.
        :param str build_url: URL of a jenkins build. If not set, method tries to find
          it using $BUILD_URL env var.
        """

        module = object.__getattribute__(self, '_module')

        if build_url is None:
            build_url = os.getenv('BUILD_URL', None)

            if build_url is None:
                raise GlueError('$BUILD_URL env var not found, was this job started by Jenkins?')

        description = description or ''

        module.jenkins_rest(build_url + '/configSubmit', **{
            'displayName': name,
            'description': description
        })

        module.debug("build name set:\n  name='{}'\n  description='{}'".format(
            name, description))

    def enable_quiet_mode(self):
        """
        Enable "quiet" mode - Jenkins will accept triggers and queue builds but it won't start them
        on slaves.
        """

        module = object.__getattribute__(self, '_module')

        return module.jenkins_rest('{}/quietDown'.format(module.option('url')))

    def disable_quiet_mode(self):
        """
        Disable "quiet" mode - Jenkins will start queued builds.
        """

        module = object.__getattribute__(self, '_module')

        return module.jenkins_rest('{}/cancelQuietDown'.format(module.option('url')))


class CIJenkins(gluetool.Module):
    """
    This modules provides connection to a jenkins instance via jenkinsapi module:
        https://jenkinsapi.readthedocs.io/en/latest/

    You can use the option '--create-jjb-config' to force creation of JJB config file.


    **Eval context**

    * ``JENKINS_URL``: URL of the Jenkins server running this module. If it cannot be determined,
      the value is ``None``. ``JENKINS_URL`` environment variable is the primary source of this
      information.
    * ``JENKINS_BUILD_ID``: ID ("number") of the Jenkins build running this module, within its
      parent job. If it cannot be determined, the value is ``None``. ``BUILD_ID`` environment
      variable is the primary source of this value.
    * ``JENKINS_BUILD_URL``: URL of the Jenkins build running this module. If it cannot be determined,
      the value is ``None``. ``BUILD_URL`` environment variable is the primary source of this value.
    * ``JENKINS_JOB_NAME``: Name of the Jenkins job the build running this module belongs to. If it
      cannot be determined, the value is ``None``. ``JOB_NAME`` environment variable is the primary
      source of this information.
    * ``JENKINS_JOB_URL``: URL of the Jenkins job the build running this module belongs to. If it
      cannot be determined, the value is ``None``. ``JOB_URL`` environment variable is the primary
      source of this information.
    """

    name = 'jenkins'
    description = 'Connect to a jenkins instance via jenkinsapi'
    requires = 'jenkinsapi'

    # shared jenkins object
    _jenkins = None

    options = {
        'create-jjb-config': {
            'help': 'Force creation Jenkins Job Builder configuration',
            'default': None,
            'metavar': 'FILE',
            'type': str
        },
        'password': {
            'help': 'Jenkins admin password (default: None)',
        },
        'url': {
            'help': 'Jenkins URL (e.g. http://localhost:8080)',
        },
        'username': {
            'help': 'Jenkins admin username (default: None)',
        },
        'no-ssl-verify': {
            'help': 'Do not verify HTTPS certificate.',
            'action': 'store_true',
            'default': False
        }
    }
    required_options = ['url']
    shared_functions = ['jenkins', 'jenkins_rest']

    def jenkins(self, reconnect=False):
        """ return jenkinsapi.Jenkins object instance """
        if reconnect:
            self.connect()

        return self._jenkins

    def jenkins_rest(self, url, **data):
        """
        Submit request to Jenkins via its http interface.

        :param str url: URL to send request to. Can be absolute, e.g. when
          caller gets its base from BUILD_URL env var, or relative, starting
          with '/'. Configured Jenkjins URL is prepended to relative URLS,
          while absolute URLs must lead to this configured Jenkins instance.
        :param dict data: data to submit to the URL.
        :returns: (response, resonse-content)
        """

        self.debug("Jenkins REST request: url='{}'\n{}".format(url, format_dict(data)))

        if url.startswith('/'):
            url = self.option('url') + url

        elif not url.startswith(self.option('url')):
            raise GlueError('Cross-site Jenkins REST request')

        if data:
            data = urllib.urlencode({
                'json': json.dumps(data)
            })

        username, password = self.option('username'), self.option('password')

        if username or password:
            request = urllib2.Request(url)
            base64string = base64.b64encode('{}:{}'.format(username, password))
            request.add_header('Authorization', 'Basic {}'.format(base64string))
            response = urllib2.urlopen(request, data)

        else:
            request = url

        response = urllib2.urlopen(request, data)
        code, content = response.getcode(), response.read()

        gluetool.log.log_blob(self.debug, 'response: {}'.format(code), content)

        if code != 200:
            raise GlueError('Jenkins REST request failed')

        return response, content

    def create_jjb_config(self):
        password = self.option('password')
        url = self.option('url')
        user = self.option('username')

        config_file = gluetool.utils.normalize_path(self.option('create-jjb-config'))
        config_dir = os.path.dirname(config_file)

        # create configuration
        config = ConfigParser.RawConfigParser()
        config.add_section('jenkins')
        config.set('jenkins', 'url', url)
        if user:
            config.set('jenkins', 'user', user)
        if password:
            config.set('jenkins', 'password', password)

        # make sure directory structure exists
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)

        # save the configuration
        with open(config_file, 'wb') as f:
            config.write(f)

        self.info("created jjb configuration in '{}'".format(config_file))

    @property
    def eval_context(self):
        """
        Variables related to Jenkins and its API and environment.

        :rtype: dict
        """

        return {
            'JENKINS_URL': os.getenv('JENKINS_URL', None),
            'JENKINS_BUILD_ID': os.getenv('BUILD_ID', None),
            'JENKINS_BUILD_URL': os.getenv('BUILD_URL', None),
            'JENKINS_JOB_NAME': os.getenv('JOB_NAME', None),
            'JENKINS_JOB_URL': os.getenv('JOB_URL', None),
        }

    def connect(self):
        password = self.option('password')
        url = self.option('url')
        user = self.option('username')
        ssl_verify = not self.option('no-ssl-verify')

        # connect to the jenkins instance
        try:
            jenkins = Jenkins(url, username=user, password=password, ssl_verify=ssl_verify)

        except RequestException as e:
            self.debug('Connection error: {}'.format(e))
            raise gluetool.GlueError("could not connect to jenkins '{}': {}".format(url, str(e)))

        self._jenkins = JenkinsProxy(jenkins, self)

    def execute(self):
        url = self.option('url')

        # create JJB configuration file if forced
        if self.option('create-jjb-config'):
            self.create_jjb_config()

        # connecto to jenkins
        self.connect()

        # be informative about the jenkins connection
        self.info('connected to jenkins \'{}\' version {}'.format(url, self._jenkins.version))
