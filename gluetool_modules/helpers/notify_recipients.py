"""
Gather and provide recipients of notifications.
"""

import re

import gluetool
from gluetool import GlueError
from gluetool.utils import cached_property, normalize_multistring_option, render_template
from gluetool.log import log_dict

# Type annotations
from typing import List


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

    For finer control of recipients on higher levels, one can use ``--recipients-map``, listing sets of actions,
    recipients and rules to control them.

    .. code-block:: yaml

       ---

       # add task issuer and 'foo' as recipients of everything (any build target)
       - rule: BUILD_TARGET.match('.*'):
         add-recipients:
           - '{{ PRIMARY_TASK.issuer }}'
           - foo

       # add 'baz' as recipient of everything, no rules applied
       - add-recipients: baz

       # remove 'bar', he does not want to deal with notifications. To remove multiple recipients, just
       # list them under the remove-recipients key, like those in add-recipients above.
       - remove-recipients: bar

       # replace 'foo.bar' with 'baz' and 'some other guy'
       - replace: 'foo\\.bar'
         with:
           - baz
           - some other guy

    Rules (``rule`` key) are optional and if present, they are evaluated by ``rules-engine`` module, possibly
    leading the module to skip the set if rules disallow them. Possible actions are ``add-recipients``,
    ``remove-recipient`` and ``replace``. Values are evaluated as Jinja2 templates within the same context
    as the rules, with an access to primary task, list of all tasks, build target and so on.
    """

    name = 'notify-recipients'
    description = 'Notification module - recipient management'

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    supported_result_types = (
        'beaker',
        'boc',
        'brew-build',
        'composetest',
        'covscan',
        'restraint',
        'rpmdiff-analysis',
        'rpmdiff-comparison',
        'rpminspect-analysis',
        'rpminspect-comparison'
    )
    result_type_names = (
        'Beaker',
        'Build-on-commit',
        'Brew build',
        'ComposeTest',
        'Covscan',
        'Restraint',
        'RPMdiff analysis',
        'RPMdiff comparison',
        'RPMinspect analysis',
        'RPMinspect comparison'
    )

    options = [
        ('Global options', {
            'force-recipients': {
                'help': 'If set, it will override all recipient settings - all notifications will go to these people',
                'metavar': 'NAMES'
            },
            'recipients': {
                'help': 'Generic, all-purpose list of recipients to notify (default: none).',
                'metavar': 'NAMES',
                'action': 'append',
                'default': []
            },
            'recipients-map': {
                'help': "File with recipients mapping (default: %(default)s).",
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

    @cached_property
    def generic_recipients(self):
        # type: () -> List[str]
        """
        List of generic recipients.

        :rtype: list(str)
        """

        return normalize_multistring_option(self.option('recipients'))

    @cached_property
    def force_recipients(self):
        """
        List of forced recipients.
        """

        return normalize_multistring_option(self.option('force-recipients'))

    @cached_property
    def recipients_map(self):
        if not self.option('recipients-map'):
            return []

        return gluetool.utils.load_yaml(self.option('recipients-map'), logger=self.logger)

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

        recipients = normalize_multistring_option(self.option('{}-notify'.format(result_type)))
        if recipients:
            self.debug('overriding recipients with result absolute notify')
            return recipients

        self.debug('using result and generic recipients')

        default_recipients = normalize_multistring_option(self.option('{}-default-notify'.format(result_type)))
        add_recipients = normalize_multistring_option(self.option('{}-add-notify'.format(result_type)))

        return default_recipients + add_recipients + self.generic_recipients

    def _recipients_overall(self):
        self.debug('collecting all available recipients')

        return sum([
            self._recipients_by_result(result_type) for result_type in self.supported_result_types
        ], [])

    def _prepare_target_recipients(self, target, context):
        """
        Prepare "target" recipients - those on the right sides of the equations,
        those we wish to use as a replacement or even as new recipients. Process
        them through the templating engine, make a list of them, and so on.

        :param target: A string or list of strings, recipients to treat.
        :param dict context: Context to use when rendering templates.
        :returns: A list of treated recipients.
        """

        # If `target` is a string, it's a single recipient => wrap it by a list
        # to allow the rest of code to work with just lists.
        if isinstance(target, str):
            target = [target]

        target = [
            render_template(recipient, logger=self.logger, **context) for recipient in target
        ]

        log_dict(self.debug, 'prepared target recipients', target)

        return target

    def _add_recipients(self, recipients, rules_context, target):
        """
        Add more recipients.

        :param list recipients: Current list of recipients.
        :param dict rules_context: Context to use for rendering new recipients.
        :param target: Recipients to add - will be processed by :py:meth:`_prepare_target_recipients`.
        :returns: Updated list of recipients.
        """

        target = self._prepare_target_recipients(target, rules_context)

        log_dict(self.debug, 'adding recipients', target)

        return recipients + target

    def _remove_recipients(self, recipients, rules_context, target):
        """
        Remove recipients.

        :param list recipients: Current list of recipients.
        :param dict rules_context: Context to use for rendering recipients we need to remove.
        :param target: Recipients to remove - will be processed by :py:meth:`_prepare_target_recipients`.
        :returns: Updated list of recipients.
        """

        target = self._prepare_target_recipients(target, rules_context)

        log_dict(self.debug, 'removing recipients', target)

        return [
            recipient for recipient in recipients if recipient not in target
        ]

    def _replace_recipients(self, recipients, rules_context, source, target):
        """
        Replace recipients.

        :param list recipients: Current list of recipients.
        :param dict rules_context: Context to use for rendering recipients we're manipulating.
        :param str source: Regexp pattern - matching recipients will be replaced with ``target``.
        :param target: Recipients to use instead of ``source`` - will be processed by
            :py:meth:`_prepare_target_recipients`.
        :returns: Updated list of recipients.
        """

        if target is None:
            raise GlueError("Don't know what to use instead of '{}'".format(source))

        target = self._prepare_target_recipients(target, rules_context)

        try:
            pattern = re.compile(source)

        except re.error as exc:
            raise GlueError("Cannot compile pattern '{}': {}".format(source, exc))

        def _replace(recipient):
            if not pattern.match(recipient):
                return [recipient]

            log_dict(self.debug, "replacing '{}' with".format(recipient), target)
            return target

        # apply _replace to every recipient - replace returns a list of 1 or more recipients,
        # simply merge them into a single list
        return sum(map(_replace, recipients), [])

    def _apply_recipients_map(self, recipients):
        """
        Walk through the recipients map and apply all requested changes to the list of recipients.
        """

        for recipients_set in self.recipients_map:
            log_dict(self.debug, 'recipients set', recipients_set)
            log_dict(self.debug, 'recipients', recipients)

            # must be prepared again for each recipients set as recipients list changes after each iteration
            rules_context = gluetool.utils.dict_update(self.shared('eval_context'), {
                'RECIPIENTS': recipients
            })

            if 'rule' in recipients_set:
                rules_result = self.shared('evaluate_rules', recipients_set.get('rule', 'False'), context=rules_context)

                if not rules_result:
                    self.debug('rules does not match, moving on')
                    continue

            if 'replace' in recipients_set:
                recipients = self._replace_recipients(recipients, rules_context,
                                                      recipients_set['replace'], recipients_set.get('with', None))

            if 'add-recipients' in recipients_set:
                recipients = self._add_recipients(recipients, rules_context, recipients_set['add-recipients'])

            if 'remove-recipients' in recipients_set:
                recipients = self._remove_recipients(recipients, rules_context, recipients_set['remove-recipients'])

        log_dict(self.debug, 'final recipients', recipients)

        return recipients

    def _finalize_recipients(self, recipients):
        """
        The final step before using recipients. Take a list of gathered recipients,
        and apply a recipient map to it. This action deals with things like symbolic
        recipients and similar stuff. Duplicities are removed after that, and the
        final list is sorted as well.

        :param list recipients: list of recipients.
        :returns: polished list of recipients.
        """

        return polish(self._apply_recipients_map(recipients))

    def notification_recipients(self, result_type=None):
        """
        Create list of recipients, based on options passed for the type of results
        this formatter handles.
        """

        recipients = self._recipients_overall() if result_type is None else self._recipients_by_result(result_type)
        recipients = self._finalize_recipients(recipients)

        self.info('recipients: {}'.format(', '.join(recipients)))

        return recipients
