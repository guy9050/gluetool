"""
Sending notifications about CI results - via e-mail.
"""

import os
import smtplib
from email.mime.text import MIMEText
from libci import CIError, Module, utils


SMTP = 'smtp.corp.redhat.com'

SENDER = 'qe-baseos-automation@redhat.com'
HARD_ERROR_CC = ['qe-baseos-automation@redhat.com']

SUBJECT = '[CI] [{result[type]}] {result[result]} for {task.nvr}, brew task {task.task_id}, \
build target {task.target.target}'


BODY_HEADER = """
Brew task:      {task.task_id}
Tested package: {task.nvr}
Build issuer:   {task.owner}@redhat.com

"""

BODY_FOOTER = """

Jenkins job:    {jenkins_job_url}


--
CI Project page: https://wiki.test.redhat.com/BaseOs/Projects/CI
"""

HARD_ERROR_MSG = """
CI pipeline crashed due to errors, and the operations team has been
informed about the issue by this message.
"""

SOFT_ERROR_MSG = """
CI pipeline was halted due to the following error:

{msg}
"""

WOW_BODY = """
Result:         {result[result]}
Beaker matrix:  {beaker_matrix_url}
"""


class Message(object):
    # pylint: disable=too-few-public-methods

    def __init__(self, module, subject=None, header=None, footer=None, body=None, recipients=None, sender=None):
        # pylint: disable=too-many-arguments
        self._module = module

        self.subject = subject or ''
        self.header = header or ''
        self.footer = footer or ''
        self.body = body or ''
        self.recipients = recipients or []
        self.sender = sender or self._module.option('sender')

    def send(self):
        if not self.subject:
            self._module.warn('Subject not set!')

        if not self.recipients:
            raise CIError('Empty list of recipients')

        content = self.header + self.body + self.footer

        msg = MIMEText(content)
        msg['Subject'] = self.subject
        msg['From'] = self.sender
        msg['To'] = ', '.join(self.recipients)

        self._module.debug("Recipients: '{}'".format(', '.join(self.recipients)))
        self._module.debug("Sender: '{}'".format(self.sender))
        self._module.debug("Subject: '{}'".format(self.subject))
        utils.log_blob(self._module.debug, 'Content', content)

        smtp = smtplib.SMTP(self._module.option('smtp-server'))
        smtp.sendmail(self.sender, self.recipients, msg.as_string())
        smtp.quit()


class Notify(Module):
    """
    This module sends notifications of CI results via e-mail.

    All options accepting e-mails - EMAILS - expect comma-separated list of e-mails (and
    will remove white space characters - 'a@b.cz,c@d.cz' or '   a@b.cz  , c@d.cz   ' are
    absolutely fine).

    For each result type, module accepts 3 options:

      - foo-default-notify

        Sets the default list of recipients. It's usualy set by CI job, by an admin, or in
        a config file, and lists people like {ISSUER} and similar "default", calculable
        recipients.

      - foo-add-notify

        Extends default list of recipients. If you're fine with sending e-mails to default
        recipients, and you wish only to add more recipients, this option is for you.

      - foo-notify

        Overrides both default and additional recipients. Module will send e-mails to this
        and only this list of recipients.

    It is possible to use "symbolic" recipients, which will be substituted with the actual
    value. So far these are available:

      - {ISSUER} - brew task issuer, with '@redhat.com' appended
    """

    name = 'notify-email'
    description = 'Notification module - e-mail'

    options = {
        'smtp-server': {
            'help': 'Outgoing SMTP server. Default: {}'.format(SMTP),
            'default': SMTP
        },
        'sender': {
            'help': 'E-mail of the sender. Default: {}'.format(SENDER),
            'default': SENDER
        },

        'hard-error-cc': {
            'help': 'Recipients to notify when hard error occures. Default: {}'.format(', '.join(HARD_ERROR_CC)),
            'metavar': 'EMAILS',
            'default': ', '.join(HARD_ERROR_CC)
        },
        'force-recipients': {
            'help': 'If set, it will override any and all recipient settings - all e-mails will go to this list',
            'metavar': 'EMAILS'
        },

        # Per-result-type notify lists
        'wow-notify': {
            'help': 'Notify only the listed recipients.',
            'metavar': 'EMAILS'
        },
        'wow-default-notify': {
            'help': 'Default list of recipients. Default: ""',
            'metavar': 'EMAILS'
        },
        'wow-add-notify': {
            'help': 'Extends default list of recipients.',
            'metavar': 'EMAILS'
        }
    }

    _formatters = None

    def option_to_mails(self, name):
        """
        Converts comma-separated list of e-mails, provided by an option, to a list.
        Trims white-space from all individual e-mails.

        :param str name: option name.
        :returns: ['email1', 'email2', ...]
        """

        mails = self.option(name)
        if not mails:
            return []

        return [s.strip() for s in mails.split(',')]

    def polish_recipients(self, recipients):
        """
        The final step before using recipients. Method substitutes all symbolic recipients
        with their actual values, removes duplicities, and sorts the list of recipients.

        :param list recipients: list of recipient e-mails.
        :returns: polished list of recipient e-mails.
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

        # remove duplicities
        recipients = {recipient: None for recipient in substituted_recipients}.keys()

        return sorted(recipients)

    @utils.cached_property
    def hard_error_cc(self):
        """
        List of recipients to add in case of hard error.
        """

        return self.option_to_mails('hard-error-cc')

    @utils.cached_property
    def force_recipients(self):
        """
        List of forced recipients.
        """

        return self.option_to_mails('force-recipients')

    @utils.cached_property
    def symbolic_recipients(self):
        """
        Mapping between symbolic recipients and the actual values.
        """

        recipients = {}

        task = self.shared('brew_task')
        if task is not None:
            recipients['ISSUER'] = '{}@redhat.com'.format(task.owner)

        return recipients

    def recipients_for_result_type(self, result_type):
        """
        Create list of recipients, based on options passed for the type of results
        this formatter handles.
        """

        self.debug("collecting recipients for a type '{}'".format(result_type))

        recipients = self.force_recipients
        if recipients:
            self.debug('overriding recipients by force')
            return recipients

        recipients = self.option_to_mails('{}-notify'.format(result_type))
        if recipients:
            self.debug('overriding recipients with absolute notify')
            return recipients

        self.debug('using default recipients')

        default_recipients = self.option_to_mails('{}-default-notify'.format(result_type))
        add_recipients = self.option_to_mails('{}-add-notify'.format(result_type))

        return default_recipients + add_recipients

    def format_result_wow(self, result, msg):
        # pylint: disable=no-self-use
        beaker_matrix_url = result['urls'].get('beaker_matrix', '<Beaker matrix URL not available>')

        msg.body = WOW_BODY.format(result=result, beaker_matrix_url=beaker_matrix_url)

    def execute(self):
        task = self.shared('brew_task')
        if not task:
            raise CIError('Unable to get brew task')

        results = self.shared('results') or []

        for result in results:
            self.debug('result:\n{}'.format(utils.format_dict(result)))

            result_type = result['type']

            formatter = getattr(self, 'format_result_{}'.format(result_type), None)
            if formatter is None:
                self.warn("Don't know how to process result of type '{}'".format(result_type))
                continue

            recipients = self.polish_recipients(self.recipients_for_result_type(result_type))
            if not recipients:
                self.warn("Result of type '{}' does not provide any recipients".format(result_type))
                continue

            self.info('Sending {} result notifications to: {}'.format(result_type, ', '.join(recipients)))

            jenkins_job_url = result['urls'].get('jenkins_job', '<Jenkins job URL not available>')

            msg = Message(self,
                          subject=SUBJECT.format(task=task, result=result),
                          header=BODY_HEADER.format(task=task),
                          footer=BODY_FOOTER.format(jenkins_job_url=jenkins_job_url),
                          recipients=recipients)

            formatter(result, msg)
            msg.send()

    def destroy(self, failure=None):
        if failure is None:
            return

        exc = failure.exc_info[1]
        soft = isinstance(exc, CIError) and exc.soft is True

        recipients = []

        # Use formatting methods as a "list" of available result types, and gather
        # their recipients
        for formatter in [name for name in dir(self) if name.startswith('format_result_')]:
            recipients += self.recipients_for_result_type(formatter[14:])

        if soft is not True:
            self.debug('Failure caused by a non-soft error')
            recipients += self.hard_error_cc

        recipients = self.polish_recipients(recipients)

        self.info('Sending failure-state notifications to: {}'.format(', '.join(recipients)))

        jenkins_job_url = os.getenv('BUILD_URL', '<Jenkins job URL not available>')
        task = self.shared('brew_task')

        if task is None:
            class DummyTask(object):
                # pylint: disable=too-few-public-methods
                def __init__(self, **kwargs):
                    self.__dict__.update(kwargs)

            task = DummyTask(task_id='<Task ID not available>', nvr='<NVR not available>',
                             owner='<Owner not available>')

        if soft:
            body = SOFT_ERROR_MSG.format(msg=exc.message)

        else:
            body = HARD_ERROR_MSG

        msg = Message(self,
                      subject='[CI] ERROR: CI crashed due to errors',
                      header=BODY_HEADER.format(task=task),
                      footer=BODY_FOOTER.format(jenkins_job_url=jenkins_job_url),
                      body=body,
                      recipients=recipients)

        msg.send()
