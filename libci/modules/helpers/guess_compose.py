import re
from libci import CIError, Module


class CIGuessCompose(Module):
    """
    Guess compose from build target. Currently these translations
    are supported (sed -r syntax):

    s/(staging-)?(rhel-[0-9]+)(.[0-9]+)?-?(z)?-candidate/\2\3.\4/
    """
    name = 'guess-distro'
    description = 'Guess distro from build target of a brew build'
    options = {
        'distro': {
            'help': 'Force usage of given compose',
        }
    }
    shared_functions = ['distro']
    distro_value = None

    def distro(self):
        """ return guessed distro value """
        return self.distro_value

    def execute(self):
        self.distro_value = self.option('distro')
        if self.distro_value is not None:
            self.info("forcing distro to '{}'".format(self.distro_value))
            return

        task = self.shared('brew_task')
        if task is None:
            raise CIError('no brew build found, did you run brew module')
        match = re.match(r'(staging-)?(rhel-[0-9]+)(.[0-9]+)?-?(z)?-candidate', task.target.target)
        if match:
            self.distro_value = '{}{}{}'.format(match.group(2),
                                                match.group(3) or '',
                                                '.' + match.group(4) if match.group(4) else '')
            self.info("guessed distro '{}' from build target '{}'".format(self.distro_value,
                                                                          task.target.target))
        else:
            raise CIError("could not translate build target '{}' to compose".format(task.target.target))
