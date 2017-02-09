"""
Sending notifications about CI results - via e-mail.
"""

import smtplib
from email.mime.text import MIMEText
from libci import CIError, Module, utils


EMAIL_SMTP = 'smtp.corp.redhat.com'

EMAIL_SENDER = 'qe-baseos-automation@redhat.com'
EMAIL_SUBJECT = 'CI: {task.component}: {task.nvr}: {result}'


EMAIL_BODY_HEADER = """
Tested package: {task.nvr}
Build issuer:   {task.owner}@redhat.com

"""

EMAIL_BODY_FOOTER = """

Jenkins job:    {jenkins_job_url}


--
CI Project page: https://wiki.test.redhat.com/BaseOs/Projects/CI
"""


EMAIL_BODY_WOW = """
Result:         {result}
Beaker matrix:  {beaker_matrix_url}
"""


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

    def _format_wow(self, result):
        # pylint: disable-msg=no-self-use
        beaker_matrix_url = result['urls'].get('beaker_matrix', '<Beaker matrix URL not available>')

        return EMAIL_BODY_WOW.format(
            result=result['result'],
            beaker_matrix_url=beaker_matrix_url)

    def execute(self):
        if not self.option('notify'):
            self.info('No notification e-mails specified')
            return

        task = self.shared('brew_task')
        if not task:
            raise CIError('Unable to get brew task')

        emails = [e.strip() for e in self.option('notify').split(',')]

        self.info('Sending job notifications to: {}'.format(', '.join(emails)))

        results = self.shared('results') or []
        smtp = smtplib.SMTP(EMAIL_SMTP)

        for result in results:
            self.debug('result:\n{}'.format(utils.format_dict(result)))

            jenkins_job_url = result['urls'].get('jenkins_job', '<Jenkins job URL not available>')

            header = EMAIL_BODY_HEADER.format(task=task)
            footer = EMAIL_BODY_FOOTER.format(jenkins_job_url=jenkins_job_url)

            subject = EMAIL_SUBJECT.format(task=task, result=result['result'])
            body = header + self._format_wow(result) + footer

            self.debug("Recipients: '{}'".format(', '.join(emails)))
            self.debug("Subject: '{}'".format(subject))
            self.debug('Body:\n{}'.format(body))

            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = EMAIL_SENDER
            msg['To'] = ', '.join(emails)

            smtp.sendmail(EMAIL_SENDER, emails, msg.as_string())

        smtp.quit()
