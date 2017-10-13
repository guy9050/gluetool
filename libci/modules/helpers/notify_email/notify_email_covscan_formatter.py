import libci
from libci import Module


class NotifyEmailCovscanFormatter(Module):
    """
    Provides formatter for Covscan results, to be sent out by a ``notify-email`` module.
    """

    name = 'notify-email-covscan-formatter'
    description = 'Custom ``notify-email`` formatter for Covscan results.'
    supported_dryrun_level = libci.ci.DryRunLevels.DRY

    options = [
        ('Template options', {
            'template-root': {
                'help': 'Directory with templates.',
                'metavar': 'DIR'
            },
            'body-template': {
                'help': 'Template of a body of the message.',
                'metavar': 'FILE'
            },
            'footer-template': {
                'help': 'Template of a custom footer.',
                'metavar': 'FILE'
            }
        })
    ]

    required_options = ('template-root', 'body-template', 'footer-template')
    shared_functions = ('notify_email_covscan_formatter',)

    def notify_email_covscan_formatter(self, notify_email, result, message):
        """
        Format message to represent the Covscan result. Updates body and footer of given message.

        :param libci.module.helpers.notify_email.Notify: ``notify-email`` module governing this operation.
        :param libci.module.static_analysis.covscan.covscan.CovscanTestResult result: Covscan result.
        :param libci.module.helpers.notify_email.Message: E-mail message container.
        """

        covscan_url = notify_email.format_result_url(result, 'covscan_url', '<Covscan URL not available>')
        brew_url = notify_email.format_result_url(result, 'brew_url', '<Covscan URL not available>')

        message.body = notify_email.render_template(self.option('body-template'), **{
            'RESULT': result,
            'COVSCAN_URL': covscan_url,
            'BREW_URL': brew_url
        })

        message.footer = notify_email.render_template(self.option('footer-template'), **{
            'RESULT': result
        })
