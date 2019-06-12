import logging
import re
import shlex

import bs4

import gluetool
from gluetool import GlueError, SoftGlueError, GlueCommandError
from gluetool.action import Action
from gluetool.log import log_dict, log_blob
from gluetool.utils import Command, render_template, dict_update
from libci.sentry import PrimaryTaskFingerprintsMixin

import qe

import gluetool_modules.libs.test_schedule


DEFAULT_WOW_OPTIONS_SEPARATOR = '#-#-#-#-#'


class NoGeneralTestPlanError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task):
        super(NoGeneralTestPlanError, self).__init__(task, "No general test plan found for '{}'".format(task.component))


class InvalidArchError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task, arch):
        super(InvalidArchError, self).__init__(
            task,
            "Invalid architecture '{}' encountered for '{}'".format(arch, task.component)
        )

        self.arch = arch


class GeneralWOWError(PrimaryTaskFingerprintsMixin, GlueError):
    def __init__(self, task, output):
        super(GeneralWOWError, self).__init__(task, "Failure during 'wow' execution: {}".format(output.stderr))


class WowCommand(Command):
    def __init__(self, module, upstream_options, ordinary_options, *args, **kwargs):
        super(WowCommand, self).__init__(*args, **kwargs)

        self.module = module
        self.upstream_options = upstream_options
        self.ordinary_options = ordinary_options

    # Following methods are exposed to ``evaluate_instructions`` shared function
    # as command callbacks.
    def add_options(self, instruction, command, argument, context):
        options = render_template(argument, logger=self.logger, **context)
        log_blob(self.debug, 'adding options', options)

        # simple split() is too dumb: '--foo "bar baz"' => ['--foo', 'bar baz']. shlex is the right tool
        # to split command-line options, it obeys quoting.
        self.options += shlex.split(options)

        log_dict(self.debug, 'current options', self.options)

    def modify_option(self, instruction, command, argument, context):
        assert isinstance(argument, dict)

        new_options = []

        if command == 'modify-upstream-option':
            option_list_name = 'upstream_options'

        elif command == 'modify-ordinary-option':
            option_list_name = 'ordinary_options'

        for option in getattr(self, option_list_name):
            option_context = dict_update(
                {},
                context,
                {
                    'OPTION': option
                }
            )

            if not self.module.shared('evaluate_rules', argument.get('rule', 'False'), context=option_context):
                new_options.append(option)
                continue

            if 'remove' in argument:
                log_blob(self.debug, 'removing option', option)
                continue

            if 'replace-with' in argument:
                log_blob(self.debug, 'replacing option', option)

                new_option = render_template(argument['replace-with'], logger=self.logger, **option_context)

                log_blob(self.debug, 'replaced with option', new_option)

                new_options += [new_option]

        # Simple self.old_list = new_list is not good enough - we must change *the content* of the list,
        # since it is shared between us and Wow module who'd add it, when done with instructions, to the
        # final command.
        getattr(self, option_list_name)[:] = new_options
        log_dict(self.debug, 'current options', new_options)

    def set_command(self, instruction, command, argument, context):
        command = render_template(argument, logger=self.logger, **context)
        self.debug("using command '{0}' to generate a job xml".format(command))

        # simple split() is too dumb: '--foo "bar baz"' => ['--foo', 'bar baz']. shlex is the right tool
        # to split command-line options, it obeys quoting.
        self.executable = shlex.split(command)

    def set_use_shell(self, instruction, command, argument, context):
        self.use_shell = bool(argument)

        self.debug('use-shell knob set to {}'.format(self.use_shell))

    def set_quote_args(self, instruction, command, argument, context):
        self.quote_args = bool(argument)

        self.debug('quote-args knob set to {}'.format(self.quote_args))


class WorkflowTomorrow(gluetool.Module):
    """
    Runs ``workflow-tomorrow`` to generate XML description of a Beaker job. Options used in ``wow``
    invocation can be controlled by following ways:

    * a configuration file, ``wow-option-map``, which lists options and conditions under which
      are these conditions added to the set used for invocation;
    * a ``wow-options`` option can be used to provide additional options which are then simply
      used;
    * when ``use-general-test-plan`` is set, module searches TCMS for a general test plan for the
      component, and if the plan exists, ``--plan ID`` is added to ``wow`` invocation.

    For each specified distro (via ``distro`` shared function), a Beaker job is created, and these
    jobs are then provided to the caller.


    wow-options-map
    ===============

    .. code-block:: yaml

       ---

       # Default options, common for all cases
       - rule: BUILD_TARGET.match('.*')
         add-options: |
           --distro "{{ DISTRO }}"
           --taskparam "BASEOS_CI=true"
           --setup beakerlib

       # Avoid s390x everywhere
       - rule: BUILD_TARGET.match('.*')
         add-options: --no-arch s390x
         add-note:
           level: warning
           text: |
             Due to circumstances out of our control, there are no s390x boxes free, therefore we have to
             skip the testing. Ping us if testing on s390x is crucial for you.

    Each set specifies a ``rule`` key which is evaluated by ``rules-engine`` module. If it evaluates to ``True``,
    the value of ``add-options`` is added to the set of ``wow`` options. It is first processed by Jinja
    templating engine.

    Rules and commands have access to full eval context, with few additional variables:

        * ``DISTRO`` - distro to create job for;
        * ``SCHEDULER`` - instance of :py:class:`gluetool.utils.Command` ``wow`` uses to build the command.
    """

    name = 'wow'
    description = 'Uses ``workflow-tomorrow`` to create an XML describing a Beaker job.'

    options = [
        ('Global options', {
            'wow-options-map': {
                'help': 'Path to a file with preconfigured ``workflow-tomorrow`` options (default: %(default)s).',
                'default': None,
                'metavar': 'FILE'
            },
            'wow-options': {
                'help': """
                        Options for ``workflow-tomorrow`` (e.g. ``--plan``, ``--run``, filter and so on.)
                        Can be used multiple times, each instance represents a distinct set of options,
                        module will try to create job XML for each of such sets (default: none).
                        """,
                'action': 'append',
                'default': []
            },
            'use-general-test-plan': {
                'help': 'Use general test plan for available build identified from primary_task shared function.',
                'action': 'store_true'
            },
            'wow-options-separator': {
                'help': """
                        Due to technical limitations of Jenkins, when jobs want to pass multiple ``--wow-options``
                        instances to this module, it is necessary to encode them into a single string. To tell them
                        apart, this SEPARATOR string is used (default: %(default)s).
                        """,
                'metavar': 'SEPARATOR',
                'type': str,
                'action': 'store',
                'default': DEFAULT_WOW_OPTIONS_SEPARATOR
            }
        })
    ]

    shared_functions = ['beaker_job_xml']

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    @gluetool.utils.cached_property
    def wow_options_map(self):
        if not self.option('wow-options-map'):
            return []

        return gluetool.utils.load_yaml(self.option('wow-options-map'), logger=self.logger)

    def sanity(self):
        # --wow-options can be specified multiple times, and, thanks to how we're letting Jenkins
        # pass it between different jobs, we even have to accept multiple wow options instances
        # in a single --wow-options specification, separated by a known separator.
        # The goal here is to get rid of these, splitting such encoded strings into the original
        # wow options, and make them transparent to the rest of the code.
        #
        # Options given to the module:
        #
        # --wow-options '--plan=foo --foo' --wow-options '--plan=foo --bar #-#-#-#-# --plan=foo --baz'
        #
        # should become a list of string like the second --wow-option was specified extra for each of its
        # constituents:
        #
        # self.option('wow-options') => [
        #   '--plan=foo --foo',
        #   '--plan=foo --bar'
        #   '--plan=foo --baz'
        # ]

        fixed_wow_options = []

        for instance in self.option('wow-options'):
            for subinstance in instance.split(self.option('wow-options-separator')):
                fixed_wow_options.append(subinstance.strip())

        # There's no way to set option value (what's self.option would return) in this stage, gluetool does
        # not support such action. Hence this may break if gluetool implementation changes. Let's make PR
        # for gluetool to provide legal way (e.g. self.option('foo', new_value)?)
        self._config['wow-options'] = fixed_wow_options

    def beaker_job_xml(self, distros=None, body_options=None, options=None, environment=None, task_params=None,
                       extra_context=None):
        """
        Run ``workflow-tomorrow`` utility to create a Beaker job XML.

        .. warning::

           This module's job is to call ``workflow-tomorrow``, pass it given options, and return
           any XML ``workflow-tomorrow`` produced. It does not care about the options, it will not
           insert any tasks to install artifact under the test - it's up to the caller to use
           the module with such options.

        Final ``workflow-tomorrow`` options are constructed from several sources, primary ones being
        one of ``body_options`` parameter and ``wow-options`` option. These represent the main "body"
        of tasks to test the artifact - or to perform any other task caller wanted to achieve - and
        are coupled with options specified by ``wow-options-map`` mapping and by ``options`` parameter.

        Caller can control what environmental variables are passed to his tasks with ``task_params`` parameter.
        Each `key/value` pair is passed to ``workflow-tomorrow`` via ``--taskparam="<key>=<value>"`` option.

        :param list(str) distros: list of distros to use. If not set, shared function ``distro`` is called,
            which is the default behavior most uses would seek. But some users may have specific needs,
            incompatible with the return value of ``distro``.
        :param list body_options: main options, usually representing a test plan or a list of tasks
            to wrap by "paperwork" elements. If not set, ``wow-options`` option is used. Even an empty
            list has more priority than ``wow-options`` option.
        :param list options: additional ordinary options for ``workflow-tomorrow``, for finer tuning of job build
            from "body" options.
        :param dict environment: if set, it will be passed to the tests via ``--environment`` option.
        :param dict task_params: if set, params will be passed to the tests via multiple
            ``--taskparam`` options.
        :param dict extra_context: if set, content is added to the context available to options map rules.
        :returns: List of elements representing Beaker jobs designed by ``workflow-tomorrow``, one
            for each distro.
        """

        self.info('running workflow-tomorrow to get job description')

        self.require_shared('distro', 'evaluate_instructions', 'tasks', 'primary_task')

        primary_task = self.shared('primary_task')

        log_dict(self.debug, 'body options', body_options)
        log_dict(self.debug, 'wow options', self.option('wow-options'))
        log_dict(self.debug, 'general test plan', self.option('use-general-test-plan'))
        log_dict(self.debug, 'ordinary options', options)
        log_dict(self.debug, 'environment', environment)
        log_dict(self.debug, 'task params', task_params)
        log_dict(self.debug, 'extra context', extra_context)
        log_dict(self.debug, 'distros', distros)

        if not body_options and not self.option('wow-options') and not self.option('use-general-test-plan'):
            raise gluetool_modules.libs.test_schedule.EmptyTestScheduleError(primary_task)

        distros = distros or self.shared('distro')
        log_dict(self.debug, 'actual distros', distros)

        # Construct the actual "body" options:
        #
        # 1. `body_options` argument - ignore everything else, use just these - caller knows what
        #    he's doing.
        # 2. --wow-options - a list of string items, each item represents one set of options
        #
        # Actual "body" options are a list of lists:
        #
        # [
        #     [ ... body_options ]
        # ]
        #
        # or:
        #
        # [
        #     ['--foo', '--bar'], # first --wow-option instance
        #     ['--baz']           # second --wow-option instance
        # ]

        if body_options is not None:
            # This is simple, body_options is already a list. Just wrap it by the outer list.
            actual_body_options = [
                body_options
            ]

        else:
            # For each --wow-options instance, add one set of options to the list, and sprinkle
            # with general test plan if needed.

            actual_body_options = [
                # --wow-options stashed strings, split it into list of options ("--foo --bar" => ["--foo", "--bar"])
                shlex.split(wow_options) for wow_options in self.option('wow-options')
            ]

            if self.option('use-general-test-plan'):
                component = primary_task.component

                try:
                    plan_id = qe.GeneralPlan(component).id

                except qe.GeneralPlanError:
                    raise NoGeneralTestPlanError(primary_task)

                for wow_options in actual_body_options:
                    wow_options.append('--plan={}'.format(plan_id))

        log_dict(self.debug, 'actual body options', actual_body_options)

        extra_context = extra_context or {}

        def _plan_job(distro, upstream_options):
            formatted_upstream_options = gluetool.utils.format_command_line([
                upstream_options
            ])

            self.debug("constructing options for distro '{}' and options {}".format(distro, formatted_upstream_options))

            #
            # prepare --environment content if available
            _environment = {}

            if self.has_shared('wow_relevancy_distro'):
                _environment['distro'] = self.shared('wow_relevancy_distro', distro)

            if self.has_shared('product'):
                _environment['product'] = self.shared('product')

            # incorporate changes demanded by user
            _environment.update(environment or {})

            command = WowCommand(
                self,
                upstream_options,
                options,
                ['bkr', 'workflow-tomorrow'], [
                    '--dry-run'  # this will make wow to print job description in XML
                ],
                logger=self.logger
            )

            context = dict_update(
                self.shared('eval_context'),
                {
                    'DISTRO': distro,
                    'SCHEDULER': command,

                    # Bare `ENVIRONMENT` would collide with common practice of using it to hold
                    # *testing environment* as understood by the pipeline. This "environment"
                    # definition is something understood by workflow-* tools.
                    'WOW_ENVIRONMENT': _environment,

                    # Let configuration see the given options
                    'UPSTREAM_OPTIONS': upstream_options,
                    'ORDINARY_OPTIONS': options
                },
                extra_context
            )

            def _add_note(instruction, command, argument, context):
                if 'text' not in argument:
                    raise GlueError('Note text is not set')

                self.shared('add_note', argument['text'], level=argument.get('level', logging.INFO))

            instruction_commands = {
                'add-options': command.add_options,
                'modify-upstream-option': command.modify_option,
                'modify-ordinary-option': command.modify_option,
                'command': command.set_command,
                'use-shell': command.set_use_shell,
                'quote-args': command.set_quote_args,
                'add-note': _add_note
            }

            self.shared('evaluate_instructions', self.wow_options_map, instruction_commands, context=context)

            #
            # add ordinary options
            if options:
                command.options += options

            #
            # add "body" workflow-options (prepared by caller)
            command.options += upstream_options

            # incorporate changes demanded by user
            if task_params:
                for name, value in task_params.iteritems():
                    command.options += ['--taskparam="{}={}"'.format(name, value)]

            try:
                with Action('running wow', parent=Action.current_action(), logger=self.logger, tags={
                    'executable': command.executable,
                    'options': command.options
                }):
                    output = command.run()

                return bs4.BeautifulSoup(output.stdout, 'xml')

            except GlueCommandError as exc:
                def _return_empty(msg):
                    self.warn(msg)
                    self.shared('add_note', msg, level=logging.WARN)

                    return None

                # Check for most common causes, and raise soft error where necessary
                if 'No relevant tasks found in test plan' in exc.output.stderr:
                    return _return_empty('No relevant tasks found for {} and options {}'.format(
                        distro,
                        formatted_upstream_options
                    ))

                if 'No recipe generated (no relevant tasks?)' in exc.output.stderr:
                    return _return_empty('No relevant tasks found for {} and options {}'.format(
                        distro,
                        formatted_upstream_options
                    ))

                if 'No valid distro/variant/arch combination found' in exc.output.stderr:
                    return _return_empty(
                        'Not possible to test on {}, no valid distro/arch combination'.format(
                            distro
                        )
                    )

                invalid_arch = re.search(r".*Invalid arch '(.*)'", exc.output.stderr, re.MULTILINE)

                if invalid_arch:
                    raise InvalidArchError(primary_task, invalid_arch.group(1))

                raise GeneralWOWError(primary_task, exc.output)

        # For each distro and "body" option set, construct one wow command (producing a job)
        jobs = []

        for distro in distros:
            for wow_options in actual_body_options:
                job = _plan_job(distro, wow_options)

                if not job:
                    continue

                jobs.append(job)

        return jobs
