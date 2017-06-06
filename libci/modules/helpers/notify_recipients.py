"""
Gather and provide recipients of notifications.
"""

from libci import Module, utils


def deduplicate(recipients):
    return {s: None for s in recipients}.keys()


def polish(recipients):
    return sorted(deduplicate(recipients))


class NotifyRecipients(Module):
    """
    This module gathers and provides lists of notification recipients to other modules.
    It focuses on people - listing usernames is its main purpose.

    All options accepting names - NAMES - expect comma-separated list of usernames (and
    will remove white space characters - 'a,c' or '   a  , c   ' are absolutely fine).

    For each result type, module accepts 3 options:

      - foo-default-notify

        Sets the default list of recipients. It's usualy set by CI job, by an admin, or in
        a config file, and lists people like {ISSUER} and similar "default", calculable
        recipients.

      - foo-add-notify

        Extends default list of recipients. If you're fine with sending notifications
        to default recipients, and you wish only to add more recipients, this option
        is for you.

      - foo-notify

        Overrides both default and additional recipients. Module will notify this and only
        this list of recipients.

    It is possible to use "symbolic" recipients, which will be substituted with the actual
    value. So far these are available:

      - {ISSUER} - brew task issuer
    """

    name = 'notify-recipients'
    description = 'Notification module - recipient management'

    supported_result_types = ('boc', 'covscan', 'restraint', 'rpmdiff', 'wow')

    options = {
        'force-recipients': {
            'help': 'If set, it will override all recipient settings - all notifications will go to these people',
            'metavar': 'NAMES'
        }
    }

    # Per-result-type notify lists
    for result_type in supported_result_types:
        options.update({
            '{}-notify'.format(result_type): {
                'help': 'Notify only the listed recipients.',
                'metavar': 'NAMES'
            },
            '{}-default-notify'.format(result_type): {
                'help': 'Default list of recipients. Default: ""',
                'metavar': 'NAMES'
            },
            '{}-add-notify'.format(result_type): {
                'help': 'Extends default list of recipients.',
                'metavar': 'NAMES'
            }
        })

    shared_functions = ['notification_recipients']

    def option_to_recipients(self, name):
        """
        Converts comma-separated list of usernames, provided by an option, to a list.
        Trims white-space from all individual usernames.

        :param str name: option name.
        :returns: ['foo', 'bar', ...]
        """

        users = self.option(name)
        if not users:
            return []

        return [s.strip() for s in users.split(',')]

    @utils.cached_property
    def force_recipients(self):
        """
        List of forced recipients.
        """

        return self.option_to_recipients('force-recipients')

    @utils.cached_property
    def symbolic_recipients(self):
        """
        Mapping between symbolic recipients and the actual values.
        """

        recipients = {}

        task = self.shared('brew_task')
        if task is not None:
            recipients['ISSUER'] = task.issuer

        return recipients

    def _recipients_by_result(self, result_type):
        """
        Create list of recipients, based on options passed for the type of results
        this formatter handles.
        """

        self.debug("collecting recipients for a type '{}'".format(result_type))

        # --force-recipients is all mighty!
        recipients = self.force_recipients
        if recipients:
            self.debug('overriding recipients by force')
            return recipients

        recipients = self.option_to_recipients('{}-notify'.format(result_type))
        if recipients:
            self.debug('overriding recipients with absolute notify')
            return recipients

        self.debug('using default recipients')

        default_recipients = self.option_to_recipients('{}-default-notify'.format(result_type))
        add_recipients = self.option_to_recipients('{}-add-notify'.format(result_type))

        return default_recipients + add_recipients

    def _recipients_overall(self):
        self.debug('collecting all available recipients')

        return sum([self._recipients_by_result(result_type) for result_type in self.supported_result_types], [])

    def _finalize_recipients(self, recipients):
        """
        The final step before using recipients. Method substitutes all symbolic recipients
        with their actual values, removes duplicities, and sorts the list of recipients.

        :param list recipients: list of recipients.
        :returns: polished list of recipients.
        """

        substituted_recipients = []

        for recipient in recipients:
            if recipient[0] != '{' or recipient[-1] != '}':
                substituted_recipients.append(recipient)
                continue

            pattern = recipient[1:-1]
            actual = self.symbolic_recipients.get(pattern, None)

            if actual is None:
                self.warn("Cannot replace recipient '{}' with the actual value".format(recipient))
                continue

            self.debug("replacing '{}' with '{}'".format(recipient, actual))
            substituted_recipients.append(actual)

        return polish(substituted_recipients)

    def notification_recipients(self, result_type=None):
        """
        Create list of recipients, based on options passed for the type of results
        this formatter handles.
        """

        recipients = self._recipients_overall() if result_type is None else self._recipients_by_result(result_type)
        recipients = self._finalize_recipients(recipients)

        self.info('recipients: {}'.format(', '.join(recipients)))

        return recipients
