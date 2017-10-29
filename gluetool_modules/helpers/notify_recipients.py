"""
Gather and provide recipients of notifications.
"""

import gluetool
from gluetool import GlueError
from gluetool.utils import cached_property, PatternMap


def deduplicate(recipients):
    return {s: None for s in recipients}.keys()


def polish(recipients):
    return sorted(deduplicate(recipients))


class NotifyRecipients(gluetool.Module):
    """
    This module gathers and provides lists of notification recipients. It focuses on people - listing usernames
    is its main purpose.

    All options accepting names - ``NAMES`` - expect comma-separated list of usernames (and will remove white
    space characters - ``'a,c'`` or ``'   a  , c   '`` are absolutely fine).

    For each result type, module accepts 3 options:

      - ``foo-default-notify``

        Sets the default list of recipients. It's usualy set by CI job, by an admin, or in a config file, and
        lists people like ``{ISSUER}`` and similar "default", calculable recipients.

      - ``foo-add-notify``

        Extends default list of recipients. If you're fine with sending notifications to default recipients, and
        you wish only to add more recipients, this option is for you.

      - ``foo-notify``

        Overrides both default and additional recipients. (gluetool.Module) will notify this and only this list
        of recipients.

    It is possible to use "symbolic" recipients, which will be substituted with the actual values. So far these
    are available:

      - ``{ISSUER}`` - task issuer
    """

    name = 'notify-recipients'
    description = 'Notification module - recipient management'

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    supported_result_types = ('beaker', 'boc', 'covscan', 'restraint', 'rpmdiff-analysis', 'rpmdiff-comparison')
    result_type_names = ('Beaker', 'Build-on-commit', 'Covscan', 'Restraint', 'RPMdiff analysis', 'RPMdiff comparison')

    options = [
        ('Global options', {
            'force-recipients': {
                'help': 'If set, it will override all recipient settings - all notifications will go to these people',
                'metavar': 'NAMES'
            },
            'mapped-recipients-map': {
                # pylint: disable=line-too-long
                'help': "Path to a pattern-map file mapping recipients (usually issuers) to another recipients. Use ';' to split multiple recipients.",
                'default': None,
                'metavar': 'PATH'
            }
        })
    ]

    # Per-result-type notify lists
    for result_name, result_type in zip(result_type_names, supported_result_types):
        options.append((
            '{} recipients'.format(result_name), {
                '{}-notify'.format(result_type): {
                    'help': 'Notify only the listed recipients.',
                    'metavar': 'NAMES',
                    'action': 'append'
                },
                '{}-default-notify'.format(result_type): {
                    'help': 'Default list of recipients. Default: ""',
                    'metavar': 'NAMES',
                    'action': 'append'
                },
                '{}-add-notify'.format(result_type): {
                    'help': 'Extends default list of recipients.',
                    'metavar': 'NAMES',
                    'action': 'append'
                }
            }
        ))

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

        # Some recipient options can be specified multiple times, in that case
        # their values are stored as a list of comma-separated lists of usernames.
        # Joining these string into a single string by a comma is good enough.
        if isinstance(users, list):
            users = ','.join(users)

        return [s.strip() for s in users.split(',')]

    @cached_property
    def force_recipients(self):
        """
        List of forced recipients.
        """

        return self.option_to_recipients('force-recipients')

    @cached_property
    def symbolic_recipients(self):
        """
        Mapping between symbolic recipients and the actual values.
        """

        if not self.has_shared('primary_task'):
            return {}

        return {
            'ISSUER': self.shared('primary_task').issuer
        }

    @cached_property
    def mapped_recipients(self):
        if not self.option('mapped-recipients-map'):
            return None

        return PatternMap(self.option('mapped-recipients-map'), logger=self.logger)

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

    def _replace_symbolic_recipients(self, recipients):
        processed = []

        for recipient in recipients:
            if recipient[0] != '{' or recipient[-1] != '}':
                processed.append(recipient)
                continue

            pattern = recipient[1:-1]
            actual = self.symbolic_recipients.get(pattern, None)

            if actual is None:
                self.warn("Cannot replace symbolic recipient '{}'".format(recipient))
                continue

            self.debug("replacing '{}' with '{}'".format(recipient, actual))
            processed.append(actual)

        return processed

    def _replace_mapped_recipients(self, recipients):
        if not self.mapped_recipients:
            return recipients

        processed = []

        for recipient in recipients:
            try:
                new_recipients = self.mapped_recipients.match(recipient)
                if not new_recipients:
                    raise GlueError("No mapping for '{}'".format(recipient))

                new_recipients = [s.strip() for s in new_recipients.split(';')]
                self.debug("replacing '{}' with '{}'".format(recipient, ', '.join(new_recipients)))

            except GlueError:
                # ignore fails, they are usualy expected
                new_recipients = [recipient]

                self.debug("Cannot replace mapped recipient '{}'".format(recipient))

            processed += new_recipients

        return processed

    def _finalize_recipients(self, recipients):
        """
        The final step before using recipients. Method substitutes all symbolic recipients
        with their actual values, removes duplicities, and sorts the list of recipients.

        :param list recipients: list of recipients.
        :returns: polished list of recipients.
        """

        symbolic_satisfied = self._replace_symbolic_recipients(recipients)
        mapped_satisfied = self._replace_mapped_recipients(symbolic_satisfied)

        return polish(mapped_satisfied)

    def notification_recipients(self, result_type=None):
        """
        Create list of recipients, based on options passed for the type of results
        this formatter handles.
        """

        recipients = self._recipients_overall() if result_type is None else self._recipients_by_result(result_type)
        recipients = self._finalize_recipients(recipients)

        self.info('recipients: {}'.format(', '.join(recipients)))

        return recipients
