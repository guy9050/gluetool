"""
Allow modules to inject enviroment variables via EnvInject module
"""

from libci import Module
from libci.utils import format_dict


DEFAULT_PROPS_FILE = 'envinject-citool.props'


class EnvInject(Module):
    """
    Provides method for exporting variables which are then injected into job's
    env variables using Jenkins EnvInject plugin.
    """

    name = 'envinject'
    description = 'Allow other modules to add variables that EnvInject module applies when job finishes.'

    options = {
        'file': {
            'help': 'Properties file, read by EnvInject',
            'default': DEFAULT_PROPS_FILE,
            'short': 'f'
        }
    }

    shared_functions = ['env']

    _variables = {}

    def env(self):
        """
        Returns a dictionary whose content will be passed to EnvInject plugin.
        """

        return self._variables

    def execute(self):
        pass

    def destroy(self, failure=None):
        self.info('Saving exported variables for EnvInject plugin')
        self.debug('variables:\n{}'.format(format_dict(self._variables)))

        with open(self.option('file'), 'w') as f:
            for key, value in self._variables.iteritems():
                f.write('{}="{}"\n'.format(key, value))
