import os
from libci import CIError, Module


class CIBrewBuildName(Module):
    """
    Use Jenkins REST API to change build name.
    """

    name = 'brew-build-name'
    description = 'Set Jenkins build name to details of brew task'

    def execute(self):
        task = self.shared('brew_task')
        if task is None:
            raise CIError('no brew task found, did you run brew module?')

        if not self.has_shared('jenkins'):
            self.warn('Jenkins API is necessary, please provide Jenkins module')
            return

        build_url = os.getenv('BUILD_URL', None)
        if build_url is None:
            self.warn('$BUILD_URL env var not found, was this job started by Jenkins?')
            return

        self.shared('jenkins').set_build_name(task.short_name)
        self.info("build name set: '{}'".format(task.short_name))
