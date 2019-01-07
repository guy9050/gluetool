"""
Sending notifications about results, via e-mail.

Each message consists of a **metadata** and a **body**. Each of these two consist of smaller pieces.

**Metadata**:

.. code-block:: none

   +------------------------------------------+
   |                                          |
   | +--------------------------------------+ |
   | | Subject (subject)                    | |
   | +--------------------------------------+ |
   |                                          |
   +------------------------------------------+

**Body**:

.. code-block:: none

   +------------------------------------------+
   |                                          |
   | +--------------------------------------+ |
   | | Body header (body-header)            | |
   | +--------------------------------------+ |
   |                                          |
   | +--------------------------------------+ |
   | | Body message                         | |
   | +--------------------------------------+ |
   |                                          |
   | +--------------------------------------+ |
   | | Body footer (body-footer)            | |
   | +--------------------------------------+ |
   |                                          |
   +------------------------------------------+

Each piece is rendered from a template that's specified by a respective option.

To render ``Body message``, one of multiple templates is chosen, depending on the situation:

    * when everything went well and there are results of tests, ``notify-email`` can ask result-specific formatter
      to render this part, thus providing representation of the results, or

    * ``soft-error-message`` in the case the pipeline was killed by a soft error, or

    * ``hard-error-message`` in the case the pipeline was killed by a hard error.

Rendered blocks are then joined together, and separated by a single blank line.

All templates are rendered with access to following variables:

    * any variable exposed by other modules via ``eval_context`` shared function,
    * ``OS`` - :py:mod:`os` module from Python's standard library
    * ``RECIPIENTS`` - list of e-mail addresses the e-mail would be send to

Some templates are given special extra variables:

    * ``subject``, ``body-header``, ``body-footer``
        * ``RESULT`` - an instance of :py:class:`libci.results.TestResult``, describing the current result

    * ``body-header``

    * ``subject``, ``soft-error-message``, ``hard-error-message``
        * ``FAILURE`` - an instance of :py:class:`gluetool.glue.Failure` describing the issues that caused pipeline
          to fail.

    * ``subject``
        * ``FAILURE_SUBJECT`` - optional subject text, provided by the soft error.

    * ``soft-error-message``, ``hard-error-message``
        * ``FAILURE_BODY`` - formatted message, provided by the soft erorr. In the case of hard errors, this
          variable will not be defined.

The result-specific bodies are rendered by helper modules, ``notify-email-*-formatter``. They use templates
as well, and these templates are given the same variables just like all templates rendered directly by
``notify-email`` module. What extra variables are available for these result-specific templates, depends
on the actual formatter module used.

In the case of soft errors, it is possible to provide extra templates for a subject text and a message. Rendered
subject text is passed to ``subject`` template, message is passed to ``soft-error-message`` template, which
can then use them as needed. Custom soft error templates are expected to be in files named
``<class name>-[subject|body].j2``. Soft errors can also override ``body header`` and ``body footer``, by
providing templates ``<class name>-[header|footer].j2``.
"""

import os

import jinja2

import gluetool
from gluetool import log, utils
from gluetool_modules.libs.mail import Message


class Notify((gluetool.Module)):
    """
    This module sends notifications of CI results via e-mail.

    Requires support module that would provide list of recipients, e.g. notify-recipients.
    """

    name = 'notify-email'
    description = 'Notification module - e-mail'
    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    options = (
        ('SMTP options', {
            'hard-error-cc': {
                'help': 'Recipients to notify when hard error occures.',
                'metavar': 'EMAILS'
            },
            'archive-bcc': {
                'help': 'If set, it will send copy of every outgoing e-mail to ``EMAILS``.',
                'metavar': 'EMAILS',
            },
            'sender': {
                'help': 'If set, it will be used as a ``From`` header of every outgoing e-mail (default: %(default)s).',
                'default': None
            },
            'reply-to': {
                'help': 'If set, it will be used as a Reply-To header of every outgoing e-mail (default: %(default)s).',
                'default': None
            },
            'email-map': {
                'help': 'Pattern map for recipient => e-mail translation.',
                'metavar': 'FILE'
            },
            'xheaders': {
                'help': 'List of comma-separated pairs <header name>:<header value> (default: none).',
                'metavar': 'HEADER:VALUE',
                'action': 'append',
                'default': []
            }
        }),
        ('Content options', {
            'add-reservation': {
                'help': 'Add reservation message for each tested machine',
                'action': 'store_true',
            }
        }),
        ('Template options', {
            'template-root': {
                'help': 'Root directory under which all templates are stored.',
                'metavar': 'DIR'
            },
            'recipient-template': {
                'help': 'Path to a subject template, relative to ``template-root``.',
                'metavar': 'FILE'
            },
            'subject-template': {
                'help': 'Path to a subject template, relative to ``template-root``.',
                'metavar': 'FILE'
            },
            'body-header-template': {
                'help': 'Path to a body header template, relative to ``template-root``.',
                'metavar': 'FILE'
            },
            'body-footer-template': {
                'help': 'Path to a body footer template, relative to ``template-root``.',
                'metavar': 'FILE'
            },
            'custom-error-message-template': {
                'help': 'Path to a template for errors with custom templates, relative to ``template-root``.',
                'metavar': 'FILE'
            },
            'error-message-template': {
                'help': 'Path to a template for errors without custom templates, relative to ``template-root``.',
                'metavar': 'FILE'
            }
        })
    )

    required_options = ('email-map',
                        'template-root',
                        'subject-template', 'body-footer-template', 'body-header-template',
                        'custom-error-message-template', 'error-message-template')

    _formatters = None

    @utils.cached_property
    def email_map(self):
        return utils.PatternMap(self.option('email-map'), logger=self.logger)

    @utils.cached_property
    def template_root(self):
        return utils.normalize_path(self.option('template-root'))

    @utils.cached_property
    def _template_env(self):
        return jinja2.Environment(
            loader=jinja2.FileSystemLoader(self.template_root),
            extensions=[
                'jinja2.ext.do'
            ]
        )

    def render_template(self, filename, **variables):
        """
        Helper method providing access to common variables we'd like to make available
        to all templates:

          * the whole eval context acquired from ``eval_context`` shared function,
          * ``notify-email`` module instance,
          * :py:mod:`os` module.

        On top of these, variables given by the caller are added, and :py:meth:`gluetool.utils.render_template`
        is called to do the rendering job.

        :param str filename: Path to file with the template. Relative to the template root
            set by ``template-root`` config option.
        :param dict variables: Variables to be injected into the template.
        """

        self.debug("rendering template from '{}'".format(filename))

        contexts = (
            self.shared('eval_context'),
            {
                'MODULE': self,
                'OS': os
            },
            variables
        )

        return utils.render_template(
            self._template_env.get_template(filename),
            **gluetool.utils.dict_update(*contexts)
        )

    def option_to_mails(self, name):
        """
        Converts comma-separated lists of e-mails, passed to an option, into a single
        list of e-mails. Flattens the original list of lists and strings, and strips
        off the white space.

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
    def archive_bcc(self):
        """
        List of archive (Bcc) recipients.
        """

        return self.option_to_mails('archive-bcc')

    @utils.cached_property
    def xheaders(self):
        xheaders_config = utils.normalize_multistring_option(self.option('xheaders'))

        xheaders = {}

        for xheader in xheaders_config:
            if not xheader or ':' not in xheader:
                raise gluetool.GlueError("'{}' is not correct format of xheader".format(xheader))

            name, value = xheader.strip().split(':')

            xheaders[name.strip()] = value.strip()

        log.log_dict(self.debug, 'X-Headers', xheaders)

        return xheaders

    def gather_reserved_guests(self, result):
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

    def format_result_url(self, result, key, default):
        """
        Format URL stored in the result. This covers collapsing adjacent '.', dealing
        with '..' and other stuff. Most of the work is offloaded to :py:func:`gluetool.utils.treat_url`
        the rest - handling missing values - is done here.

        :param libci.results.Result result: result providing URLs.
        :param str key: key into `result`'s `url` field - denotes what URL caller wants.
        :param str default: default value returned when the URL is not present.
        """

        if key not in result.urls:
            return default

        return utils.treat_url(result.urls[key], logger=self.logger)

    def _format_result(self, result):
        self.debug('result:\n{}'.format(result))

        result_type = result.test_type

        recipients = self.shared('notification_recipients', result_type=result_type)
        if not recipients:
            self.warn("Result of type '{}' does not provide any recipients".format(result_type))
            return None

        recipients = [self.email_map.match(name) for name in recipients]

        formatter_name = 'notify_email_{}_formatter'.format(result_type.replace('-', '_'))

        if not self.has_shared(formatter_name):
            # reset formatter_name to signal we have no formatter
            # pylint: disable=line-too-long
            self.warn("Don't know how to format result of type '{}', formatter '{}' not available".format(result_type, formatter_name),
                      sentry=True)

            formatter_name = None

        self.info('Sending {} result notifications to: {}'.format(result_type, ', '.join(recipients)))

        def _render_template(filename, **variables):
            """
            YARF aka. Yet Another Render Function :)

            We want to inject additional variables, ``RECIPIENTS`` and ``RESULT``, to all contexts. This
            function does that, and calls :py:meth:`render_template` to inject even more variables and actually
            render the template.
            """

            return self.render_template(filename, RECIPIENTS=recipients, RESULT=result, **variables)

        msg = Message(recipients=recipients, bcc=self.archive_bcc,
                      sender=self.option('sender'), reply_to=self.option('reply-to'),
                      subject=_render_template(self.option('subject-template')),
                      header=_render_template(self.option('body-header-template')),
                      footer=_render_template(self.option('body-footer-template')),
                      xheaders=self.xheaders)

        if formatter_name is not None:
            self.shared(formatter_name, self, result, msg)

        return msg

    def execute(self):
        self.require_shared('send_email')

        results = self.shared('results') or []

        for result in results:
            msg = self._format_result(result)

            if not msg:
                continue

            self.shared('send_email', msg)

    def _format_failure(self, failure):
        recipients = [self.email_map.match(name) for name in self.shared('notification_recipients')]

        if failure.soft is not True:
            self.debug('Failure caused by a non-soft error')

            recipients += self.hard_error_cc

        self.info('Sending failure-state notifications to: {}'.format(', '.join(recipients)))

        def _render_template(filename, **variables):
            """
            YARF #2 aka. Yet Another Render Function, Part Deux :)

            We want to inject additional variables, ``RECIPIENTS`` and ``FAILURE``, to all contexts. This
            function does that, and calls :py:meth:`render_template` to inject even more variables and actually
            render the template.
            """

            return self.render_template(filename, RECIPIENTS=recipients, FAILURE=failure, **variables)

        body_header = _render_template(self.option('body-header-template'))
        body_footer = _render_template(self.option('body-footer-template'))

        # Any subclass of CIError (which covers all soft errors by default) can provide its own templates
        # - but not the CIError or SoftCIError, these are way too generic.
        if isinstance(failure.exception, gluetool.GlueError) \
                and failure.exception.__class__ not in (gluetool.GlueError, gluetool.SoftGlueError):
            body_template = 'custom-error-message-template'

            klass_name = failure.exc_info[0].__name__

            def _render_exception_template(postfix, default=None):
                template_filename = '{}-{}.j2'.format(klass_name, postfix)

                if not os.path.exists(os.path.join(self.template_root, template_filename)):
                    if default is None:
                        self.warn("Exception '{}' does not provide template for '{}'".format(klass_name, postfix),
                                  sentry=True)
                        return ''

                    return default

                return _render_template(template_filename)

            body_header = _render_exception_template('header', default=body_header)
            failure_subject = _render_exception_template('subject')
            failure_body = _render_exception_template('body')
            body_footer = _render_exception_template('footer', default=body_footer)

        else:
            body_template = 'error-message-template'
            failure_subject, failure_body = '', ''

        subject = _render_template(self.option('subject-template'), **{
            'FAILURE_SUBJECT': failure_subject
        })

        body = _render_template(self.option(body_template), **{
            'FAILURE_BODY': failure_body
        })

        return Message(subject=subject,
                       header=body_header,
                       footer=body_footer,
                       body=body,
                       recipients=recipients,
                       bcc=self.archive_bcc,
                       xheaders=self.xheaders,
                       sender=self.option('sender'),
                       reply_to=self.option('reply-to'))

    def destroy(self, failure=None):
        if failure is None or isinstance(failure.exc_info[1], SystemExit):
            return

        if not self.require_shared('notification_recipients', warn_only=True):
            return

        self.require_shared('send_email')

        self.shared('send_email', self._format_failure(failure))
