import re
import shlex

import gluetool

from gluetool.utils import run_command


class TCMSRun(gluetool.Module):
    """
    Create or use an existing TCMS run - whose plan is specified by user configuration - and
    fill it with data from Beaker jobs which the pipeline ran, e.g. via ``beaker`` module.

    Module runs ``tcms-run`` to create (or recycle) TCMS run, with options specified by a system-wide
    mapping file (``--tcms-run-options-map``) and by the user (``--tcms-run-options``). It should
    acquire a TCMS run ID.

    List of results is inspected, and Beaker job IDs are distilled from results of ``beaker`` type.
    ``tcms-results`` is then started on the run and jobs to upload results into the run. It is possible
    to set system-wide (``--tcms-results-options-map``) and user (``--tcms-results-options``) options
    for ``tcms-results``.

    User must set some ``tcms-run`` options, since at least ``--plan N`` would be different among
    tested components. ``tcms-results`` works by default, user might want to set options like ``--no-avc``.

    Mapping files support rules (via ``rules-engine``) to control application use of respective sections.
    A single action, ``add-options`` is supported, its content is passed to the program as its options.

    .. code-block:: yaml

       # Default options, common for all cases
       - rule: True  # not necessary, without rules ``True`` is the default => section without rules is applied
         add-options: |
           --duplicate
           --id
           --summary "{{ PRIMARY_TASK.id }}: {{ NVR }}{% if PRIMARY_TASK.scratch %} (scratch){% endif %}"
    """

    name = 'tcms-run'
    description = 'Upload results from a Beaker job to the TCMS run.'

    options = {
        'tcms-run-options-map': {
            'help': 'Mapping file with system-wide options for ``tcms-run``. Supports rules & templates.',
            'default': None
        },
        'tcms-run-options': {
            'help': 'Additional options for ``tcms-run``, e.g. ``--plan``.'
        },
        'tcms-results-options-map': {
            'help': 'Mapping file with system-wide options for ``tcms-results``. Supports rules & templates.',
            'default': None
        },
        'tcms-results-options': {
            'help': 'Additional options for ``tcms-results``, e.g. ``--no-avc``.',
            'default': ''
        }
    }

    required_options = ('tcms-run-options',)

    @gluetool.utils.cached_property
    def tcms_run_options_map(self):
        if not self.option('tcms-run-options-map'):
            return []

        return gluetool.utils.load_yaml(self.option('tcms-run-options-map'), logger=self.logger)

    @gluetool.utils.cached_property
    def tcms_results_options_map(self):
        if not self.option('tcms-results-options-map'):
            return []

        return gluetool.utils.load_yaml(self.option('tcms-results-options-map'), logger=self.logger)

    def _create_run(self):
        command = ['tcms-run']

        context = self.shared('eval_context')

        # Options set by a configuration
        for options_set in self.tcms_run_options_map:
            gluetool.log.log_dict(self.debug, 'options set', options_set)

            if not self.shared('evaluate_rules', options_set.get('rule', 'True'), context=context):
                self.debug('rule does not match, moving on')
                continue

            if 'add-options' in options_set:
                add_options = gluetool.utils.render_template(options_set['add-options'], logger=self.logger, **context)
                gluetool.log.log_blob(self.debug, 'adding options', add_options)

                # simple split() is too dumb: '--foo "bar baz"' => ['--foo', 'bar baz']. shlex is the right tool
                # to split command-line options, it obeys quoting.
                command += shlex.split(add_options)

        command += shlex.split(self.option('tcms-run-options'))

        output = run_command(command, logger=self.logger)

        try:
            return int(output.stdout.strip())

        except ValueError:
            raise gluetool.GlueError('Cannot find run ID in output of `tcms-run` (missing `--id` option?)')

    def _push_result(self, run, jobs):
        command = [
            'tcms-results',
            '--run', str(run),
            '--job', ','.join([str(job) for job in jobs])
        ]

        context = self.shared('eval_context')

        # Options set by a configuration
        for options_set in self.tcms_results_options_map:
            gluetool.log.log_dict(self.debug, 'options set', options_set)

            if not self.shared('evaluate_rules', options_set.get('rule', 'True'), context=context):
                self.debug('rule does not match, moving on')
                continue

            if 'add-options' in options_set:
                add_options = gluetool.utils.render_template(options_set['add-options'], logger=self.logger, **context)
                gluetool.log.log_blob(self.debug, 'adding options', add_options)

                # simple split() is too dumb: '--foo "bar baz"' => ['--foo', 'bar baz']. shlex is the right tool
                # to split command-line options, it obeys quoting.
                command += shlex.split(add_options)

        command += shlex.split(self.option('tcms-results-options'))

        run_command(command, logger=self.logger)

    def execute(self):
        self.require_shared('evaluate_rules', 'results')

        run = self._create_run()
        self.info('uploading results to TR#{}'.format(run))

        jobs = []

        for result in self.shared('results'):
            if result.test_type != 'beaker':
                continue

            if 'beaker_matrix' not in result.urls:
                self.warn('Result provides no Beaker matrix URL', sentry=True)
                continue

            match = re.match(r'.*?job_ids=([0-9\+]+).*', result.urls['beaker_matrix'])
            if match is None:
                self.warn("Cannot find job IDs in matrix URL '{}'".format(result.urls['beaker_matrix']), sentry=True)
                return

            jobs += [int(job) for job in match.group(1).split('+')]

        self._push_result(run, jobs)
