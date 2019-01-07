import os.path
import traceback

import gluetool


class Dashboard(gluetool.Module):
    """
    Provides - and logs - "dashboard" URL - an URL somewhere in the wild that, when opened,
    shows nice overview of testing performed by different CI system for the primary artifact.
    """

    name = 'dashboard'
    description = 'Provides "dashboard" URL'

    options = {
        'dashboard-url-template': {
            'help': 'Template used for creating a Dashboard URL'
        }
    }

    required_options = ('dashboard-url-template',)

    @property
    def dashboard_url(self):
        return gluetool.utils.render_template(self.option('dashboard-url-template'), **self.shared('eval_context'))

    @property
    def eval_context(self):
        # To render dashboard URL, we need eval context. When asked to provide eval context, we want to
        # include dashboard URL. Voila, infiniterecursion: eval_context => dasboard_url => eval_context => ...
        #
        # To avoid this infinite chain, on entry to our eval_context we check for recursion, and if the
        # current frame is not the first occurence of *our* eval_context property, we immediately return
        # an empty context - the first call tried to render the URL and called eval_context again,
        # it is perfectly fine to not add our DASHBOARD_URL variable to the context, it haven't been
        # rendered yet anyway...
        #
        # For frames of the stack, we check whether a frame lies in this very function. If that's true
        # for any frame except the last one (the one we're running this check in at this moment), it
        # means this instance of `eval_context` is not the first one, and therefore we're allowed to
        # return empty context to avoid infinite recursion.
        #
        # Note: this check might be a good candidate for upstream - it could be useful if Glue's shared
        # `eval_context` would be safe for this use case, or provide this check for the `eval_context`
        # implementations (decorator? @nonrecursive, ...?), but it looks like this is so far the only case.
        # Until next one appears, let's keep it private.

        file_split = os.path.splitext(__file__)[0]

        if any([
                (f[2] == 'eval_context' and os.path.splitext(f[0])[0] == file_split)
                for f in traceback.extract_stack()
        ]):
            return {}

        # pylint: disable=unused-variable
        __content__ = {  # noqa
            'DASHBOARD_URL': """
                             URL of the dashboard page containing details of CI runs for the artifact.
                             """
        }

        return {
            'DASHBOARD_URL': self.dashboard_url
        }

    def execute(self):
        if not self.dashboard_url:
            self.warn('Dashboard URL seems to be empty')
            return

        self.info('For more info on the artifact, see {}'.format(self.dashboard_url))
