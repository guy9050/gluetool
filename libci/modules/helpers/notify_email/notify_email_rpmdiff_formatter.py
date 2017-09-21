import libci
from libci import Module


class NotifyEmailRPMdiffFormatter(Module):
    """
    Provides formatter for RPMdiff results, to be sent out by a ``notify-email`` module.
    """

    name = 'notify-email-rpmdiff-formatter'
    description = 'Custom ``notify-email`` formatter for RPMdiff results.'
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
    shared_functions = ('notify_email_rpmdiff_analysis_formatter', 'notify_email_rpmdiff_comparison_formatter')

    def _notify_email_rpmdiff_formatter(self, notify_email, result, message):
        """
        Format message to represent the RPMdiff result. Updates body and footer of given message.

        :param libci.module.helpers.notify_email.Notify: ``notify-email`` module governing this operation.
        :param libci.module.static_analysis.rpmdiff.rpmdiff.RpmdiffTestResult result: RPMdiff result.
        :param libci.module.helpers.notify_email.Message: E-mail message container.
        """

        jenkins_build_url = notify_email.format_result_url(result, 'jenkins_build',
                                                           '<Jenkins build URL not available>')

        message.body = notify_email.render_template(self.option('body-template'), **{
            'RESULT': result,
            'JENKINS_BUILD_URL': jenkins_build_url
        })

        message.footer = notify_email.render_template(self.option('footer-template'), **{
            'RESULT': result
        })

    # pylint: disable=invalid-name
    notify_email_rpmdiff_analysis_formatter = _notify_email_rpmdiff_formatter
    notify_email_rpmdiff_comparison_formatter = _notify_email_rpmdiff_formatter
