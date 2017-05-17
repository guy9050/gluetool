"""
Sending notifications about CI results - via e-mail.
"""

import os
import smtplib
from email.mime.text import MIMEText

from mako.template import Template

import libci
from libci import CIError, Module, utils


DEFAULT_MODULE_DOCS_URL = 'http://liver3.lab.eng.brq.redhat.com/~citool-doc/'
DEFAULT_FRONTEND_URL_TEMPLATE = 'http://baseos-ci.engineering.redhat.com/index.php/b-{JOB_NAME}-{BUILD_ID}'


SMTP = 'smtp.corp.redhat.com'

SENDER = 'qe-baseos-automation@redhat.com'
HARD_ERROR_CC = ['qe-baseos-automation@redhat.com']
ARCHIVE_CC = ['qe-baseos-automation@redhat.com']

SUBJECT = '[CI] [{result.test_type}] [{result.overall_result}] {task.nvr}, brew task {task.task_id}, \
build target {task.target.target}'

SUBJECT_RESERVE = '[CI] [RESERVATION] [{result.test_type}] [{result.overall_result}] {task.nvr}, \
brew task {task.task_id}, build target {task.target.target}'

BODY_HEADER = """
Brew task:      {task.task_id}
Tested package: {task.nvr}
Build issuer:   {task.issuer}@redhat.com
Summary:        {summary_url}
"""

BODY_FOOTER = """
--
CI Project page: https://docs.engineering.redhat.com/display/CI/User+Documentation
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
Result:         {result.overall_result}
Beaker matrix:  {beaker_matrix_url}

{reserved}

{fails}
"""

RPMDIFF_BODY = """
Result:         {result.overall_result}
RPMdiff run:    {rpmdiff_url}

RPMdiff CI Test Plan: http://url.corp.redhat.com/rpmdiff-in-ci
"""

RESTRAINT_BODY = """
Result:         {result.overall_result}

{reserved}

{fails}
"""

COVSCAN_BODY = """
Tested build:   {brew_url}

Fixed defects:  {result.fixed}
Added defects:  {result.added}

Final result:   {result.overall_result}

Covscan url:          {covscan_url}
Covscan wiki:         https://engineering.redhat.com/trac/CoverityScan/wiki/covscan
Covscan CI Test Plan: https://url.corp.redhat.com/covscan-in-ci
"""

RESERVED_BODY = Template("""
Reserved machine(s) (password: redhat):

% for guest in guests:
  ssh root@${guest}
%endfor
""")

FAILS_BODY = Template("""
<%
  import re
  import tabulate
%>

Failed tests:

% for name, runs in fails.iteritems():
  ${name} failed on:

${ re.sub(r'(.*)', r'    \\1', tabulate.tabulate(fails_tabulate(name, runs), tablefmt='plain')) }


%endfor
""")


class Message(object):
    # pylint: disable=too-few-public-methods

    def __init__(self, module, subject=None, header=None, footer=None, body=None, recipients=None, cc=None,
                 sender=None):
        # pylint: disable=too-many-arguments
        self._module = module

        self.subject = subject or ''
        self.header = header or ''
        self.footer = footer or ''
        self.body = body or ''
        self.recipients = recipients or []
        # pylint: disable=invalid-name
        self.cc = cc or []
        self.sender = sender or self._module.option('sender')

    def send(self):
        if not self.subject:
            self._module.warn('Subject not set!')

        if not self.recipients:
            self._module.warn('Empty list of recipients')
            self.recipients = ['nobody@redhat.com']

        content = self.header + self.body + self.footer

        msg = MIMEText(content)
        msg['Subject'] = self.subject
        msg['From'] = self.sender
        msg['To'] = ', '.join(self.recipients)
        msg['Cc'] = ', '.join(self.cc)

        self._module.debug("Recipients: '{}'".format(', '.join(self.recipients)))
        self._module.debug("Bcc: '{}'".format(', '.join(self.cc)))
        self._module.debug("Sender: '{}'".format(self.sender))
        self._module.debug("Subject: '{}'".format(self.subject))
        utils.log_blob(self._module.debug, 'Content', content)

        try:
            smtp = smtplib.SMTP(self._module.option('smtp-server'))

            smtp.sendmail(self.sender, self.recipients + self.cc, msg.as_string())
            smtp.quit()

        except smtplib.SMTPException as exc:
            msg = 'Cannot send e-mail, SMTP raised an exception: {}'.format(str(exc))

            self._module.warn(msg)
            self._module.ci.sentry_submit_warning(msg)


class Notify(Module):
    """
    This module sends notifications of CI results via e-mail.

    Requires support module that would provide list of recipients, e.g. notify-recipients.
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

        'shorten-urls': {
            'help': 'Shorten long URLs, using https://url.corp.redhat.com/',
            'action': 'store_true',
            'default': False
        },
        'frontend-url': {
            'help': """Template for constructing links to frontend website. URL is created using template passed
to this option, and process environmental variables (default: {})""".format(DEFAULT_FRONTEND_URL_TEMPLATE),
            'metavar': 'URL-TEMPLATE',
            'default': DEFAULT_FRONTEND_URL_TEMPLATE
        },

        'hard-error-cc': {
            'help': 'Recipients to notify when hard error occures. Default: {}'.format(', '.join(HARD_ERROR_CC)),
            'metavar': 'EMAILS',
            'default': ', '.join(HARD_ERROR_CC)
        },
        'archive-cc': {
            # pylint: disable=line-too-long
            'help': 'If set, it will send copy of every outgoing e-mail to EMAILS (default: {})'.format(', '.join(ARCHIVE_CC)),
            'metavar': 'EMAILS',
            'default': ', '.join(ARCHIVE_CC)
        },
        'add-reservation': {
            'help': 'Add reservation message for each tested machine',
            'action': 'store_true',
        },
        'add-frontend-url': {
            'help': 'Use frontend URL instead of Jenkins when pointing user to the results.',
            'action': 'store_true'
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

    @utils.cached_property
    def hard_error_cc(self):
        """
        List of recipients to add in case of hard error.
        """

        return self.option_to_mails('hard-error-cc')

    @utils.cached_property
    def archive_cc(self):
        """
        List of archive (Bcc) recipients.
        """

        return self.option_to_mails('archive-cc')

    def _shorten_url(self, url):
        """
        Depending on module options, this method may or may not shorten the url.

        :param str url: URL to shorten.
        :rtype: str
        :returns: Shortened URL if ``--shorten-urls`` was set, original URL otherwise.
        """

        if self.option('shorten-urls'):
            return utils.treat_url(url, shorten=True, logger=self.logger)

        return utils.treat_url(url)

    def _gather_failed_tests(self, result):
        """
        Gather short summary for each failed test instance. The summary will then be used
        to provide user with more information in the notification e-mails.

        :param libci.result.Result result: result to inspect. So far, only ``workflow-tomorrow`
          and ``restraint`` provide the summaries, other result types do not support this
          feature.

        :rtype: dict
        :returns: a dictionary, where task names are the keys, with lists as values:

          .. code-block:: python

             [
               (<URL of test source git repository>),
               {
                 'arch': <architecture the test ran on>,
                 'status': 'Completed', ...,
                 'result': 'PASS', 'FAIL', ...
                 'testout.log': <optional URL of test output log>
               },
               ...
             ]
        """

        self.debug('searching for failed tests')

        if result.overall_result not in ('PASS', 'FAIL'):
            # only PASS and FAIL results provide list of individual case runs,
            # e.g. ERROR result does not carry this information.
            self.debug('no tasks to inspect (overall result is {})'.format(result.overall_result))
            return {}

        fails = {}

        for name, runs in result.payload.iteritems():
            self.debug('consider task {}'.format(name))

            for run in runs:
                status, result = str(run['bkr_status']), str(run['bkr_result'])

                if status.lower() == 'completed' and result.lower() == 'pass':
                    continue

                name_parts = name.split('/')

                if name not in fails:
                    name_parts = name.split('/')

                    # guess git URL from test name... this is not good, this is so bad
                    # there's not even a cathegory for this approach...
                    if len(name_parts) >= 4:
                        # E.g. /tools/strace/Regressions/bz12345678-foo-bar-crashed
                        test_src = 'http://pkgs.devel.redhat.com/cgit/tests/{}/tree/{}'.format(
                            name_parts[2], '/'.join(name_parts[3:]))
                        test_src = self._shorten_url(test_src)

                    else:
                        self.warn("Cannot assign GIT address to a test '{}'".format(name))
                        test_src = '<Unknown GIT address>'

                    fails[name] = [(test_src,)]

                run_summary = {
                    'arch': run['bkr_arch'],
                    'status': run['bkr_status'],
                    'result': run['bkr_result'],
                    'host': run['connectable_host']
                }

                for log in run['bkr_logs']:
                    if str(log['name']).lower() not in ('testout.log', 'taskout.log'):
                        continue

                    run_summary['testout.log'] = self._shorten_url(log['href'])
                    break

                fails[name].append(run_summary)

        self.debug('found fails:\n{}'.format(utils.format_dict(fails)))

        return fails

    def _gather_reserved_guests(self, result):
        """
        Gather unique connectable host from results. This list will then be used
        to provide user with reservation information in the notification e-mails.

        :param libci.result.Result result: result to inspect. So far, only ``workflow-tomorrow`
          and ``restraint`` provide the summaries, other result types do not support this
          feature.

        :rtype: set
        :returns: a set with uniqe connectable guests
        """

        guests = set()
        for name, runs in result.payload.iteritems():
            self.debug('consider task {}'.format(name))

            guests.update({run['connectable_host'] for run in runs})

        return guests

    def _format_result_url(self, result, key, default):
        """
        Format URL stored in the result. This covers collapsing adjacent '.', dealing
        with '..', and even shortening the URL when asked to do so. Most of the work
        is offloaded to :py:func:`libci.utils.treat_url` via :py:meth:`NotifyEmail._shorten_url`,
        the rest - handling missing values - is done here.

        :param libci.results.Result result: result providing URLs.
        :param str key: key into `result`'s `url` field - denotes what URL caller wants.
        :param str default: default value returned when the URL is not present.
        """

        if key not in result.urls:
            return default

        return self._shorten_url(result.urls[key])

    def _format_beaker_like_body(self, result, msg, body_template, **kwargs):
        # pylint: disable=no-self-use
        adding_reservation = self.option('add-reservation') is not False

        # list failed tests if there are such
        fails_body = ''
        fails = self._gather_failed_tests(result)

        if fails:
            # creating a table of failed runs for a test is quite unclear when written directly in
            # the template, therefore providing more readable helper
            def fails_tabulate(name, runs):
                table = []

                if adding_reservation:
                    for run in runs[1:]:
                        table += [
                            ('Server:', '{} ({})'.format(run['host'], run['arch'])),
                            ('Output:', run.get('testout.log', '<Not available>'))
                        ]

                else:
                    for run in runs[1:]:
                        table += [('Output ({}):'.format(run['arch']), run.get('testout.log', '<Not available>'))]

                table += [('Test source:', runs[0][0])]

                if adding_reservation:
                    table += [('Test location on machine:', '/mnt/tests/{}'.format(name))]

                return table

            fails_body = utils.render_template(FAILS_BODY, fails=fails, fails_tabulate=fails_tabulate)

        # add reservation info if requested by user

        reserved_body = ''
        if adding_reservation:
            reserved_body = utils.render_template(RESERVED_BODY, guests=self._gather_reserved_guests(result))

        msg.body = body_template.format(result=result,
                                        fails=fails_body.strip(),
                                        reserved=reserved_body.strip(),
                                        **kwargs)

    def format_result_wow(self, result, msg):
        # pylint: disable=no-self-use
        beaker_matrix_url = self._format_result_url(result, 'beaker_matrix', '<Beaker matrix URL not available>')

        self._format_beaker_like_body(result, msg, WOW_BODY, beaker_matrix_url=beaker_matrix_url)

    def format_result_rpmdiff(self, result, msg):
        # pylint: disable=no-self-use
        rpmdiff_url = self._format_result_url(result, 'rpmdiff_url', '<RPMdiff URL not available>')

        msg.body = RPMDIFF_BODY.format(result=result, rpmdiff_url=rpmdiff_url)

    def format_result_restraint(self, result, msg):
        # pylint: disable=no-self-use

        self._format_beaker_like_body(result, msg, RESTRAINT_BODY)

    def format_result_covscan(self, result, msg):
        # pylint: disable=no-self-use

        covscan_url = self._format_result_url(result, 'covscan_url', '<Covscan URL not available>')
        brew_url = self._format_result_url(result, 'brew_url', '<Covscan URL not available>')

        msg.body = COVSCAN_BODY.format(result=result, covscan_url=covscan_url, brew_url=brew_url)

    def _get_summary_url(self, result):
        jenkins_build_url = None
        frontend_url = None

        if 'jenkins_build' in result.urls:
            jenkins_build_url = self._shorten_url(result.urls['jenkins_build'])

        if 'baseosci_frontend' in result.urls:
            frontend_url = result.urls['baseosci_frontend']

        else:
            frontend_url_template = self.option('frontend-url')

            try:
                frontend_url = self._shorten_url(frontend_url_template.format(**os.environ))

            except KeyError as exc:
                msg = "Cannot create frontend URL from template '{}': {} variable not set".format(
                    frontend_url_template, exc.args[0])

                self.warn(msg)
                self.ci.sentry_submit_warning(msg)

        if self.option('add-frontend-url'):
            self.debug('asked to use frontend url')

            if frontend_url:
                return frontend_url

            self.warn('Asked to add frontend URL but that is not set')

        if jenkins_build_url:
            self.debug('jenkins build url exists, using it as summary url')

            return jenkins_build_url

        return '<Summary URL not available>'

    def execute(self):
        task = self.shared('brew_task')
        if not task:
            raise CIError('Unable to get brew task')

        results = self.shared('results') or []
        reserve = self.option('add-reservation')

        for result in results:
            self.debug('result:\n{}'.format(result))

            result_type = result.test_type

            formatter = getattr(self, 'format_result_{}'.format(result_type), None)
            if formatter is None:
                self.warn("Don't know how to process result of type '{}'".format(result_type))
                continue

            recipients = self.shared('notification_recipients', result_type=result_type)
            if not recipients:
                self.warn("Result of type '{}' does not provide any recipients".format(result_type))
                continue

            recipients = ['{}@redhat.com'.format(name) for name in recipients]
            self.info('Sending {} result notifications to: {}'.format(result_type, ', '.join(recipients)))

            summary_url = self._get_summary_url(result)

            subject = SUBJECT_RESERVE if reserve else SUBJECT

            msg = Message(self,
                          subject=subject.format(task=task, result=result),
                          header=BODY_HEADER.format(task=task, summary_url=summary_url).strip(),
                          footer=BODY_FOOTER.strip(),
                          recipients=recipients,
                          cc=self.archive_cc)

            # we're sure formatter *is* callable
            # pylint: disable=not-callable
            formatter(result, msg)
            msg.send()

    def destroy(self, failure=None):
        if failure is None or isinstance(failure.exc_info[1], SystemExit):
            return

        exc = failure.exc_info[1]

        recipients = ['{}@redhat.com'.format(user) for user in self.shared('notification_recipients')]

        if failure.soft is not True:
            self.debug('Failure caused by a non-soft error')

            recipients += self.hard_error_cc

        self.info('Sending failure-state notifications to: {}'.format(', '.join(recipients)))

        result = libci.results.TestResult('dummy', 'ERROR')
        summary_url = self._get_summary_url(result)

        task = self.shared('brew_task')

        if task is None:
            class DummyTask(object):
                # pylint: disable=too-few-public-methods
                def __init__(self, **kwargs):
                    self.__dict__.update(kwargs)

            task = DummyTask(task_id='<Task ID not available>', nvr='<NVR not available>',
                             owner='<Owner not available>', issuer='<No issuer available>')

        # default body header and footer
        body_header = BODY_HEADER.format(task=task, summary_url=summary_url)
        body_footer = BODY_FOOTER

        if failure.soft:
            message = exc.render()

            subject = ['[CI]']
            if exc.MODULE_NAME:
                subject.append('[{}]'.format(exc.MODULE_NAME))
            subject.append('[{}]'.format(exc.STATUS))
            subject.append('{}'.format(message['subject']))
            subject = ' '.join(subject)
            body = SOFT_ERROR_MSG.format(msg=message['body'])

            # override body header and footer if defined in the soft exception
            if exc.BODY_HEADER is not None:
                body_header = exc.BODY_HEADER
            if exc.BODY_FOOTER is not None:
                body_footer = exc.BODY_FOOTER

        else:
            subject = '[CI] [ABORT] CI pipeline crashed, operations team was notified'
            body = HARD_ERROR_MSG

        msg = Message(self,
                      subject=subject,
                      header=body_header,
                      footer=body_footer,
                      body=body,
                      recipients=recipients,
                      cc=self.archive_cc)

        msg.send()
