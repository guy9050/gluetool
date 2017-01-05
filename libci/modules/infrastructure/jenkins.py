import ConfigParser
import libci
import os

from jenkinsapi.jenkins import Jenkins
# from libci import Module
from libci import libciError
from requests.exceptions import RequestException

JJB_CONFIG = os.path.expanduser('~/.config/jenkins_jobs/jenkins_jobs.ini')


class CIJenkins(libci.Module,object):
    """This modules provides connection to a jenkins instance via jenkinsapi module:
    https://jenkinsapi.readthedocs.io/en/latest/

It is also able to create/replace Jenkins Job Builder configuration file
    {0}
with option '--create-jjb-config'.
""".format(JJB_CONFIG)

    name = 'jenkins'
    description = 'Connect to a jenkins instance via jenkinsapi'
    requires = 'jenkinsapi'

    # shared jenkins object
    jenkins_instance = None

    options = {
        'create-jjb-config': {
            'help': 'Create/Replace Jenkins Job Builder configuration',
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
    shared_functions = ['jenkins']

    def jenkins(self, reconnect=False):
        """ return jenkinsapi.Jenkins object instance """
        if reconnect:
            self.connect()
        return self.jenkins_instance

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
            error = 'could not connect to jenkins \'{}\''.format(url)
            error += ': {}'.format(str(e))
            raise libciError(error)

    def execute(self):
        create_config = self.option('create-jjb-config')
        url = self.option('url')

        # create JJB configuration file if needed
        if create_config:
            self.create_jjb_config()

        # connecto to jenkins
        self.connect()

        # be informative about the jenkins connection
        version = self.jenkins_instance.version
        msg = 'connected to jenkins \'{}\' version {}'.format(url, version)
        self.info(msg)
