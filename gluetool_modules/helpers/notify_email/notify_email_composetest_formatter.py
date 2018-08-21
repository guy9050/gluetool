import gluetool


class NotifyEmailComposetestFormatter((gluetool.Module)):
    """
    Provides formatter for ComposeTest results, to be sent out by a ``notify-email`` module.
    """

    name = 'notify-email-composetest-formatter'
    description = 'Custom ``notify-email`` formatter for ComposeTest results.'
    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    options = [
        ('Template options', {
            'template-root': {
                'help': 'Directory with templates.',
                'metavar': 'DIR'
            },
            'subject-template': {
                'help': 'Path to a subject template.',
                'metavar': 'FILE'
            },
            'header-template': {
                'help': 'Template of a header of the message.',
                'metavar': 'FILE'
            },
            'body-template': {
                'help': 'Template of a body of the message.',
                'metavar': 'FILE'
            },
            'footer-template': {
                'help': 'Template of a custom footer.',
                'metavar': 'FILE'
            },
        })
    ]

    required_options = ('template-root', 'body-template', 'footer-template')
    shared_functions = ('notify_email_composetest_formatter',)

    def notify_email_composetest_formatter(self, notify_email, result, message):
        """
        Format message to represent the ComposeTest result. Updates body and footer of given message.

        :param gluetool_module.helpers.notify_email.Notify: ``notify-email`` module governing this operation.
        :param gluetool_module.testing.composetest.composetest.ComposeTestResult result: ComposeTest result.
        :param gluetool_module.helpers.notify_email.Message: E-mail message container.
        """

        message.subject = notify_email.render_template(self.option('subject-template'), **{
            'RESULT': result,
        })

        message.header = notify_email.render_template(self.option('header-template'))

        message.body = notify_email.render_template(self.option('body-template'), **{
            'RESULT': result,
        })

        message.footer = notify_email.render_template(self.option('footer-template'))
