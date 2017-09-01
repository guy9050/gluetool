import os
from libci import Module


class CIBrewBuildName(Module):
    """
    Use Jenkins REST API to change build name.
    """

    name = 'brew-build-name'
    description = 'Set Jenkins build name to details of brew task'

    options = {
        'testing-thread-length': {
            'help': 'Testing thread will be shortened to N characters. Use ``-1`` for full length.',
            'type': int,
            'metavar': 'N',
            'default': -1
        }
    }

    def execute(self):
        self.require_shared('primary_task')

        if not self.require_shared('jenkins'):
            return

        task = self.shared('primary_task')

        build_url = os.getenv('BUILD_URL', None)
        if build_url is None:
            self.warn('$BUILD_URL env var not found, was this job started by Jenkins?', sentry=True)
            return

        name = task.short_name

        if self.has_shared('thread_id'):
            thread_id = self.shared('thread_id')
            if self.option('testing-thread-length') != -1:
                thread_id = thread_id[0:self.option('testing-thread-length')]

            name = '{}:{}'.format(thread_id, name)

        else:
            self.warn('Testing thread ID not found')

        self.shared('jenkins').set_build_name(name)
        self.info("build name set: '{}'".format(name))
