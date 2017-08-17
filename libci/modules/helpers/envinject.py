"""
Allow modules to inject enviroment variables via EnvInject module
"""

from libci import Module
from libci.log import format_dict


DEFAULT_PROPS_FILE = 'envinject-citool.props'


class EnvInject(Module):
    """
    Provides method for exporting variables which are then injected into job's
    env variables using Jenkins EnvInject plugin.
    """

    name = 'envinject'
    description = 'Allow other modules to add variables that EnvInject module applies when job finishes.'

    options = {
        ('f', 'file'): {
            'help': 'Properties file, read by EnvInject',
            'default': DEFAULT_PROPS_FILE
        }
    }

    shared_functions = ['env']

    _variables = {}

    def env(self):
        """
        Returns a dictionary whose content will be passed to EnvInject plugin.
        """

        return self._variables

    def destroy(self, failure=None):
        if not self.option('file'):
            self.debug('Do not save exported variables for EnvInject plugin: no file provided')
            return

        self.info('Saving exported variables for EnvInject plugin')
        self.debug('variables:\n{}'.format(format_dict(self._variables)))

        with open(self.option('file'), 'w') as f:
            for key, value in self._variables.iteritems():
                f.write('{}="{}"\n'.format(key, value))
