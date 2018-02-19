import os

import gluetool


class JenkinsBuildName(gluetool.Module):
    """
    Use Jenkins REST API to change build name.
    """

    name = ['jenkins-build-name', 'brew-build-name', 'copr-build-name']
    description = 'Set Jenkins build name.'

    options = {
        'name': {
            'help': 'Build name template.',
            'type': str
        }
    }

    required_options = ['name']

    def execute(self):
        if not self.require_shared('jenkins'):
            return

        build_url = os.getenv('BUILD_URL', None)
        if build_url is None:
            self.warn('$BUILD_URL env var not found, was this job started by Jenkins?', sentry=True)
            return

        context = self.shared('eval_context')

        thread_id = self.shared('thread_id')
        context.update({
            'THREAD_ID': thread_id
        })

        if thread_id is None:
            self.warn('Testing thread ID not found')

        name = gluetool.utils.render_template(self.option('name'), logger=self.logger, **context)

        self.shared('jenkins').set_build_name(name)
        self.info("build name set: '{}'".format(name))
