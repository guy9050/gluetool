import gluetool


class NotifyEmailBrewBuildFormatter((gluetool.Module)):
    """
    Provides formatter for Brew build results, to be sent out by a ``notify-email`` module.
    """

    name = 'notify-email-brew-build-formatter'
    description = 'Custom ``notify-email`` formatter for Brew build results.'
    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    options = [
        ('Template options', {
            'template-root': {
                'help': 'Directory with templates.',
                'metavar': 'DIR'
            },
            'body-template': {
                'help': 'Template of a body of the message.',
                'metavar': 'FILE'
            }
        })
    ]

    required_options = ('template-root', 'body-template')
    shared_functions = ('notify_email_brew_build_formatter',)

    # pylint: disable=invalid-name
    def notify_email_brew_build_formatter(self, notify_email, result, message):
        """
        Format message to represent the Brew build result. Updates body and footer of given message.

        :param gluetool_module.helpers.notify_email.Notify: ``notify-email`` module governing this operation.
        :param gluetool_module.testing.pull_request_builder.brew_builder.BrewBuildTestResult result: Brew build result.
        :param gluetool_module.helpers.notify_email.Message: E-mail message container.
        """

        message.body = notify_email.render_template(self.option('body-template'), **{
            'RESULT': result
        })
