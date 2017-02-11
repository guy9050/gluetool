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

JJB_CONFIG = os.path.expanduser('~/.config/jenkins_jobs/jenkins_jobs.ini')
JJB_GLOBAL_CONFIG = os.path.expanduser('/etc/jenkins_jobs/jenkins_jobs.ini')


class CIJenkins(libci.Module):
    """This modules provides connection to a jenkins instance via jenkinsapi
module:
    https://jenkinsapi.readthedocs.io/en/latest/

This module will also create Jenkins Job Builder configuration file
    {0}
if not found in paths '{0}' or '{1}'.

You can use the option '--create-jjb-config' to force creation of \'{0}\' file.
""".format(JJB_CONFIG, JJB_GLOBAL_CONFIG)

    name = 'jenkins'
    description = 'Connect to a jenkins instance via jenkinsapi'
    requires = 'jenkinsapi'

    # shared jenkins object
    jenkins_instance = None

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

    }
    required_options = ['url']
    shared_functions = ['jenkins', 'jenkins_rest']

    def jenkins(self, reconnect=False):
        """ return jenkinsapi.Jenkins object instance """
        if reconnect:
            self.connect()
        return self.jenkins_instance

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

        data = urllib.urlencode({
            'json': json.dumps(data)
        })

        response = urllib2.urlopen(url, data)
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

        # connect to the jenkins instance
        try:
            self.jenkins_instance = Jenkins(url,
                                            username=user,
                                            password=password)
        except RequestException as e:
            self.debug('Connection error: {}'.format(e))
            raise libci.CIError("could not connect to jenkins '{}': {}".format(url, str(e)))

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
        version = self.jenkins_instance.version
        msg = 'connected to jenkins \'{}\' version {}'.format(url, version)
        self.info(msg)
