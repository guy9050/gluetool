from libci import CIError, Module

DEF_PROPS_FILE = 'build-name.props'


class CIBrewBuildName(Module):
    """
    Create an Jenkins property file with details about brew task,
    intended to be used for setting build name via the BuildNameSetter
    plugin.

    Examples of contents of the output file:

    BUILD_NAME=S:12445186:kernel-3.10.0-514.9.1.el7.prebuild
    BUILD_NAME=12449561:sssd-1.13.3-56.el6

    Example of the section needed to be added to the JJB yaml:

    builders:
        - inject:
            properties-file: build-name.props
    wrappers:
        - build-name:
            name: ${BUILD_NAME}
    """
    name = 'brew-build-name'
    description = 'Create an Jenkins property file with details about brew task'

    options = {
        'output': {
            'help': 'Output properties file, usable for EnvInject',
            'default': DEF_PROPS_FILE,
            'short': 'o',
        }
    }

    def execute(self):
        task = self.shared('brew_task')
        output = self.option('output')
        if task is None:
            raise CIError('no brew task found, did you run brew module?')

        with open(output, 'w') as f:
            f.write('BUILD_NAME={}'.format(task.short_name))

        self.info("build name '{}' saved to property file '{}'".format(task.short_name, output))
