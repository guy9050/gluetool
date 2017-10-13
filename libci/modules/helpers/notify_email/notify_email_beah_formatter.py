import itertools

import libci
from libci import Module


class NotifyEmailBeahFormatter(Module):
    """
    Provides formatter for Beaker and Restraint results, to be sent out by a ``notify-email`` module.
    """

    name = 'notify-email-beah-formatter'
    description = 'Custom ``notify-email`` formatter for Beaker and Restraint results.'
    supported_dryrun_level = libci.ci.DryRunLevels.DRY

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
    shared_functions = ('notify_email_beaker_formatter', 'notify_email_restraint_formatter')

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

                if name not in fails:
                    name_parts = name.split('/')

                    # guess git URL from test name... this is not good, this is so bad
                    # there's not even a category for this approach...
                    if name_parts[1] == 'distribution':
                        test_src = 'http://pkgs.devel.redhat.com/cgit/tests/distribution/tree/{}'.format(
                            '/'.join(name_parts[2:]))
                        test_src = libci.utils.treat_url(test_src, logger=self.logger)

                    elif len(name_parts) >= 4:
                        # E.g. /tools/strace/Regressions/bz12345678-foo-bar-crashed
                        test_src = 'http://pkgs.devel.redhat.com/cgit/tests/{}/tree/{}'.format(
                            name_parts[2], '/'.join(name_parts[3:]))
                        test_src = libci.utils.treat_url(test_src, logger=self.logger)

                    else:
                        self.warn("Cannot assign GIT address to a test '{}'".format(name), sentry=True)
                        test_src = '<Unknown GIT address>'

                    fails[name] = [(test_src,)]

                run_summary = {
                    'arch': run['bkr_arch'],
                    'status': run['bkr_status'],
                    'result': run['bkr_result'],
                    'host': run['connectable_host'],
                    'phases': []
                }

                # finds the first log entry with matching 'name', or None if there's no such entry
                first_full_log = next(itertools.ifilter(
                    lambda x: x['name'].lower() in ('testout.log', 'taskout.log'), run['bkr_logs']), None)

                if first_full_log is not None:
                    run_summary['testout.log'] = libci.utils.treat_url(first_full_log['href'], logger=self.logger)

                phase_names = [phase['name'] for phase in run['bkr_phases']]

                for i, phase in enumerate(run['bkr_phases']):
                    if str(phase['result']).lower() == 'pass':
                        continue

                    run_summary['phases'].append({
                        'name': phase['name'],
                        'result': phase['result'],
                        'follows': phase_names[i - 1],
                        'logs': [libci.utils.treat_url(log['href'], logger=self.logger) for log in phase['logs']]
                    })

                fails[name].append(run_summary)

        self.debug('found fails:\n{}'.format(libci.log.format_dict(fails)))

        return fails

    def _format_beah_body(self, notify_email, result, message, beaker_matrix_url=None):
        adding_reservation = bool(notify_email.option('add-reservation'))

        # list failed tests if there are such
        fails_body = ''
        fails = self._gather_failed_tests(result)

        if fails:
            fails_body = notify_email.render_template(self.option('fails-template'), **{
                'FAILS': fails
            })

        # add reservation info if requested by user
        reservation_body = ''
        if adding_reservation:
            reservation_body = notify_email.render_template(self.option('reservation-template'), **{
                'GUESTS': notify_email.gather_reserved_guests(result)
            })

        message.body = notify_email.render_template(self.option('body-template'), **{
            'RESULT': result,
            'RESERVATION': reservation_body,
            'FAILS': fails_body,
            'BEAKER_MATRIX_URL': beaker_matrix_url
        })

    # pylint: disable=invalid-name
    def notify_email_beaker_formatter(self, notify_email, result, message):
        """
        Format message to represent the Beaker result. Updates body of given message.

        :param libci.modules.helpers.notify_email.Notify: ``notify-email`` module governing this operation.
        :param libci.modules.testing.beaker.beaker.BeakerTestResult result: Beaker result.
        :param libci.modules.helpers.notify_email.Message: E-mail message container.
        """

        beaker_matrix_url = notify_email.format_result_url(result, 'beaker_matrix', '<Beaker matrix URL not available>')

        self._format_beah_body(notify_email, result, message, beaker_matrix_url=beaker_matrix_url)

    def notify_email_restraint_formatter(self, notify_email, result, message):
        """
        Format message to represent the Restraint result. Updates body of given message.

        :param libci.modules.helpers.notify_email.Notify: ``notify-email`` module governing this operation.
        :param libci.modules.testing.restraint.runner.RestraintTestResult result: Restraint result.
        :param libci.modules.helpers.notify_email.Message: E-mail message container.
        """

        self._format_beah_body(notify_email, result, message)
