import docker
import requests

import gluetool


DEFAULT_VERSION = '1.26'


class Docker(gluetool.Module):
    """
    Provides access to Docker server via Python API.
    """

    name = 'docker'
    description = 'Provides access to Docker server via Python API.'

    options = {
        'protocol-version': {
            'help': 'Docker protocol version to force (default: %(default)s)',
            'default': DEFAULT_VERSION
        }
    }

    shared_functions = ['docker']

    def __init__(self, *args, **kwargs):
        super(Docker, self).__init__(*args, **kwargs)

        self._docker = None

    def docker(self):
        return self._docker

    def execute(self):
        self._docker = docker.from_env(version=self.option('protocol-version'))

        try:
            version = self._docker.version()

        except requests.exceptions.ConnectionError as exc:
            raise gluetool.GlueError('Cannot connect to a docker server: {}'.format(str(exc)))

        self.debug('connected to docker server:\n{}'.format(gluetool.utils.format_dict(version)))
        self.info('connected to docker server, version {}, API version {}'.format(version['Version'],
                                                                                  version['ApiVersion']))
