import base64
import ConfigParser
import json
import os
import urllib
import urllib2

from jenkinsapi.jenkins import Jenkins
# from libci import Module
from requests.exceptions import RequestException

import libci
from libci import CIError
from libci.proxy import Proxy

JJB_CONFIG = os.path.expanduser('~/.config/jenkins_jobs/jenkins_jobs.ini')
JJB_GLOBAL_CONFIG = os.path.expanduser('/etc/jenkins_jobs/jenkins_jobs.ini')


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

    _CUSTOM_METHODS = ('set_build_name',)

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
                raise CIError('$BUILD_URL env var not found, was this job started by Jenkins?')

        description = description or ''

        module.jenkins_rest(build_url + '/configSubmit', **{
            'displayName': name,
            'description': description
        })

        module.debug("build name set:\n  name='{}'\n  description='{}'".format(
            name, description))


class CIJenkins(libci.Module):
    """
    This modules provides connection to a jenkins instance via jenkinsapi module:
        https://jenkinsapi.readthedocs.io/en/latest/

    This module will also create Jenkins Job Builder configuration file {0},
    if not found in paths '{0}' or '{1}'.

    You can use the option '--create-jjb-config' to force creation of \'{0}\' file.
    """.format(JJB_CONFIG, JJB_GLOBAL_CONFIG)

    name = 'jenkins'
    description = 'Connect to a jenkins instance via jenkinsapi'
    requires = 'jenkinsapi'

    # shared jenkins object
    _jenkins = None

    options = {
        'create-jjb-config': {
            'help': 'Force creation Jenkins Job Builder configuration',
            'action': 'store_true',
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

        self.debug("Jenkins REST request: url='{}'\n{}".format(url, libci.utils.format_dict(data)))

        if url.startswith('/'):
            url = self.option('url') + url

        elif not url.startswith(self.option('url')):
            raise CIError('Cross-site Jenkins REST request')

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

        libci.utils.log_blob(self.debug, 'response: {}'.format(code), content)

        if code != 200:
            raise CIError('Jenkins REST request failed')

        return response, content

    def create_jjb_config(self):
        password = self.option('password')
        url = self.option('url')
        user = self.option('username')
        config_dir = os.path.dirname(JJB_CONFIG)

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
        with open(JJB_CONFIG, 'wb') as f:
            config.write(f)
        self.info('created jjb configuration in \'{}\''.format(JJB_CONFIG))

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
            raise libci.CIError("could not connect to jenkins '{}': {}".format(url, str(e)))

        self._jenkins = JenkinsProxy(jenkins, self)

    def execute(self):
        create_config = self.option('create-jjb-config')
        url = self.option('url')

        # create JJB configuration file if forced
        if create_config:
            self.create_jjb_config()

        # create JJB configuration file if it does not exist
        if not os.path.exists(JJB_CONFIG) and \
                not os.path.exists(JJB_GLOBAL_CONFIG):
            self.create_jjb_config()

        # connecto to jenkins
        self.connect()

        # be informative about the jenkins connection
        self.info('connected to jenkins \'{}\' version {}'.format(url, self._jenkins.version))
