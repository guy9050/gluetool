"""
Sending notifications about CI results - via e-mail.
"""

import os
import smtplib
from email.mime.text import MIMEText
from libci import CIError, Module, utils


SMTP = 'smtp.corp.redhat.com'

SENDER = 'qe-baseos-automation@redhat.com'
CC = 'qe-baseos-automation@redhat.com'

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


BODY_WOW = """
Result:         {result[result]}
Beaker matrix:  {beaker_matrix_url}
"""


BODY_ERROR = """
CI pipeline crashed due to infrastructure error, and its operations team has been
informed about the issue by this message.
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
        self.sender = sender or SENDER

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
        self._module.debug('Content:\n{}'.format(content))

        smtp = smtplib.SMTP(SMTP)
        smtp.sendmail(self.sender, self.recipients, msg.as_string())
        smtp.quit()


def _format_wow(result, msg):
    beaker_matrix_url = result['urls'].get('beaker_matrix', '<Beaker matrix URL not available>')

    msg.body = BODY_WOW.format(result=result, beaker_matrix_url=beaker_matrix_url)


class Notify(Module):
    """
    This module sends notifications of CI results via e-mail.
    """

    name = 'notify'
    description = 'Notification module - e-mail'

    options = {
        'notify': {
            'help': 'Comma-separated list of e-mails to notify when job finishes.'
        }
    }

    @utils.cached_property
    def recipients(self):
        return [CC] + [e.strip() for e in self.option('notify').split(',')]

    def execute(self):
        if not self.option('notify'):
            self.info('No notification e-mails specified')
            return

        task = self.shared('brew_task')
        if not task:
            raise CIError('Unable to get brew task')

        self.info('Sending job notifications to: {}'.format(', '.join(self.recipients)))

        results = self.shared('results') or []

        for result in results:
            self.debug('result:\n{}'.format(utils.format_dict(result)))

            jenkins_job_url = result['urls'].get('jenkins_job', '<Jenkins job URL not available>')

            msg = Message(self,
                          subject=SUBJECT.format(task=task, result=result),
                          header=BODY_HEADER.format(task=task),
                          footer=BODY_FOOTER.format(jenkins_job_url=jenkins_job_url),
                          recipients=self.recipients)

            _format_wow(result, msg)
            msg.send()

    def destroy(self, failure=None):
        if failure is None:
            return

        self.info('Sending failure-state notifications to: {}'.format(', '.join(self.recipients)))

        jenkins_job_url = os.getenv('BUILD_URL', '<Jenkins job URL not available>')
        task = self.shared('brew_task')

        if task is None:
            class DummyTask(object):
                # pylint: disable=too-few-public-methods
                def __init__(self, **kwargs):
                    self.__dict__.update(kwargs)

            task = DummyTask(task_id='<Task ID not available>', nvr='<NVR not available>',
                             owner='<Owner not available>')

        msg = Message(self,
                      subject='[CI] ERROR: CI crashed due to infrastructure errors',
                      header=BODY_HEADER.format(task=task),
                      footer=BODY_FOOTER.format(jenkins_job_url=jenkins_job_url),
                      body=BODY_ERROR,
                      recipients=self.recipients)

        msg.send()
