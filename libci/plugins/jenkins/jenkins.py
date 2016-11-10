from libci import Plugin
from libci import libciError
from libci import retry

from requests.exceptions import RequestException
from jenkinsapi.jenkins import Jenkins

class CIJenkins(Plugin):
    name = 'jenkins'
    desc = 'Connect to jenkins instance'
    requires = 'jenkinsapi'

    # shared jenkins object
    jenkins_instance = None

    options = {
        'url': {
            'help': 'Jenkins URL (e.g. http://localhost:8080)',
        },
    }
    required_options = ['url']
    shared_functions = ['jenkins']

    def jenkins(self):
        return self.jenkins_instance

    def execute(self):
        url = self.option('url')
        try:
            self.jenkins_instance = Jenkins(url)
        except RequestException as e:
            self.debug('Connection error: {}'.format(e))
            error = 'could not connect to jenkins \'{}\''.format(url)
            error += ': {}'.format(str(e))
            raise libciError(error)

        version = self.jenkins_instance.version
        msg = 'Connected to jenkins \'{}\' version {}'.format(url, version)
        self.info(msg)
