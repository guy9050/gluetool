import gluetool


def _gather_failed_tests(result):
    """
    Gather short summary for each failed test cases. The summary will then be used
    to provide user with more information in the notification e-mails.

    :param bs4.element.Tag result: result to inspect.
    :rtype: list(bs4.element.Tag)
    :returns: list of failed test cases.
    """

    return [
        testcase
        for testcase in result.find_all('testcase')
        if testcase.find('error') or testcase.find('failure')
    ]


def _gather_reserved_guests(result):
    """
    Gather unique connectable host from results. This list will then be used
    to provide user with reservation information in the notification e-mails.

    :param bs4.element.Tag result: result to inspect.
    :rtype: list(str)
    :returns: a list of unique hostnames of connectable guests.
    """

    guests = [
        el['value'] for el in result.find_all('property', attrs={'name': 'baseosci.connectable_host'})
    ]

    return list(set(guests))


class NotifyEmailxUnitFormatter(gluetool.Module):
    """
    Provides formatter for xUnit results, to be sent out by a ``notify-email`` module.
    """

    name = 'notify-email-xunit-formatter'
    description = 'Custom ``notify-email`` formatter for xUnit results.'
    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    options = [
        ('Template options', {
            'template-root': {
                'help': 'Directory with templates.',
                'metavar': 'DIR'
            },
            'body-template': {
                'help': 'Path to a body template, relative to ``template-root``.',
                'metavar': 'FILE'
            },
            'fails-template': {
                'help': 'Path to a "failures" template, relative to ``template-root``.',
                'metavar': 'FILE'
            },
            'reservation-template': {
                'help': 'Path to a "machines reserved" template, relative to ``template-root``.',
                'metavar': 'FILE'
            }
        })
    ]

    required_options = ('template-root', 'body-template', 'fails-template', 'reservation-template')
    shared_functions = ('notify_email_xunit_formatter',)

    def _format_beah_body(self, notify_email, result, message):
        # options checker does not see options outside of this module, and cannot check through `notify_email` var
        # pylint: disable=gluetool-unknown-option
        adding_reservation = bool(notify_email.option('add-reservation'))

        # list failed tests if there are such
        fails_body = ''
        fails = _gather_failed_tests(result)

        if fails:
            fails_body = notify_email.render_template(self.option('fails-template'), **{
                'FAILS': fails
            })

        # add reservation info if requested by user
        reservation_body = ''
        if adding_reservation:
            reservation_body = notify_email.render_template(self.option('reservation-template'), **{
                'GUESTS': _gather_reserved_guests(result)
            })

        message.body = notify_email.render_template(self.option('body-template'), **{
            'RESULT': result,
            'RESERVATION': reservation_body,
            'FAILS': fails_body
        })

    def notify_email_xunit_formatter(self, notify_email, result, message):
        """
        Format message to represent the Beaker result. Updates body of given message.

        :param gluetool_modules.helpers.notify_email.Notify: ``notify-email`` module governing this operation.
        :param bs4.element.Tag result: Beaker result.
        :param gluetool_modules.helpers.notify_email.Message: E-mail message container.
        """

        self._format_beah_body(notify_email, result, message)
