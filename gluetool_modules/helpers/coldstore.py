import gluetool

import gluetool_modules.libs


class ColdStore(gluetool.Module):
    """
    Provides - and logs - "cold store" URL - an URL somewhere in the wild that, when opened,
    contains artifacts produced by this particular pipeline invocation.
    """

    name = 'coldstore'
    description = 'Provides "cold store" URL'

    options = {
        'coldstore-url-template': {
            'help': 'Template used for creating a cold store URL.'
        }
    }

    required_options = ('coldstore-url-template',)

    @property
    def coldstore_url(self):
        return gluetool.utils.render_template(self.option('coldstore-url-template'), **self.shared('eval_context'))

    @property
    def eval_context(self):
        # To render cold store URL, we need eval context. When asked to provide eval context, we want to
        # include cold store URL. Voila, infinite recursion: eval_context => dasboard_url => eval_context => ...

        if gluetool_modules.libs.is_recursion(__file__, 'eval_context'):
            return {}

        # pylint: disable=unused-variable
        __content__ = {  # noqa
            'COLDSTORE_URL': """
                             URL of the "cold store" page containing artifacts of this particula pipeline.
                             """
        }

        return {
            'COLDSTORE_URL': self.coldstore_url
        }

    def execute(self):
        if not self.coldstore_url:
            self.warn('Cold store URL seems to be empty')
            return

        self.info('For the pipeline artifacts, see {}'.format(self.coldstore_url))
