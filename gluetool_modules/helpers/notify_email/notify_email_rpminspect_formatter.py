import gluetool


class NotifyEmailRPMinspectFormatter(gluetool.Module):
    """
    Provides formatter for RPMinspect results, to be sent out by a ``notify-email`` module.
    """

    name = 'notify-email-rpminspect-formatter'
    description = 'Custom ``notify-email`` formatter for RPMinspect results.'
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
            },
            'footer-template': {
                'help': 'Template of a custom footer.',
                'metavar': 'FILE'
            }
        })
    ]

    required_options = ('template-root', 'body-template', 'footer-template')
    shared_functions = ('notify_email_rpminspect_analysis_formatter', 'notify_email_rpminspect_comparison_formatter')

    def _notify_email_rpminspect_formatter(self, notify_email, result, message):
        """
        Format message to represent the RPMinspect result. Updates body and footer of given message.

        :param gluetool_module.helpers.notify_email.Notify: ``notify-email`` module governing this operation.
        :param gluetool_.module.static_analysis.rpminspect.rpminspect.RpminspectTestResult result: RPMinspect result.
        :param gluetool_module.helpers.notify_email.Message: E-mail message container.
        """

        jenkins_build_url = notify_email.format_result_url(result, 'jenkins_build',
                                                           '<Jenkins build URL not available>')

        message.body = notify_email.render_template(self.option('body-template'), **{
            'RESULT': result,
            'JENKINS_BUILD_URL': jenkins_build_url
        })

        message.footer = notify_email.render_template(self.option('footer-template'))

    notify_email_rpminspect_analysis_formatter = _notify_email_rpminspect_formatter
    notify_email_rpminspect_comparison_formatter = _notify_email_rpminspect_formatter
