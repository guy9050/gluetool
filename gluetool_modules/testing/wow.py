import shlex

import bs4
import qe

import gluetool
from gluetool import GlueError, SoftGlueError, GlueCommandError
from gluetool.utils import Command, render_template
from libci.sentry import PrimaryTaskFingerprintsMixin


class NoTestAvailableError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task):
        super(NoTestAvailableError, self).__init__(task, 'No tests provided for the component')


class NoGeneralTestPlanError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task):
        super(NoGeneralTestPlanError, self).__init__(task, "No general test plan found for '{}'".format(task.component))


class GeneralWOWError(PrimaryTaskFingerprintsMixin, GlueError):
    def __init__(self, task, output):
        super(GeneralWOWError, self).__init__(task, "Failure during 'wow' execution: {}".format(output.stderr))


class WowCommand(Command):
    # Following methods are exposed to ``evaluate_instructions`` shared function
    # as command callbacks.
    def add_options(self, instruction, command, argument, context):
        # pylint: disable=unused-argument

        options = render_template(argument, logger=self.logger, **context)
        gluetool.log.log_blob(self.debug, 'adding options', options)

        # simple split() is too dumb: '--foo "bar baz"' => ['--foo', 'bar baz']. shlex is the right tool
        # to split command-line options, it obeys quoting.
        self.options += shlex.split(options)

    def set_command(self, instruction, command, argument, context):
        # pylint: disable=unused-argument

        command = render_template(argument, logger=self.logger, **context)
        self.debug("using command '{0}' to generate a job xml".format(command))

        # simple split() is too dumb: '--foo "bar baz"' => ['--foo', 'bar baz']. shlex is the right tool
        # to split command-line options, it obeys quoting.
        self.executable = shlex.split(command)

    def set_use_shell(self, instruction, command, argument, context):
        # pylint: disable=unused-argument

        self.use_shell = bool(argument)

        self.debug('use-shell knob set to {}'.format(self.use_shell))

    def set_quote_args(self, instruction, command, argument, context):
        # pylint: disable=unused-argument

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

    Each set specifies a ``rule`` key which is evaluated by ``rules-engine`` module. If it evaluates to ``True``,
    the value of ``add-options`` is added to the set of ``wow`` options. It is first processed by Jinja
    templating engine. ``wow`` module provides few variables to both rules and options, thus making
    module's runtime context available to the config file writer. Following variables are available:

        * ``DISTRO``
        * ``BUILD_TARGET``
        * ``PRIMARY_TASK``
        * ``TASKS``
    """

    name = 'wow'
    description = 'Uses ``workflow-tomorrow`` to create an XML describing a Beaker job.'

    options = [
        ('Global options', {
            'wow-options-map': {
                'help': 'Path to a file with preconfigured ``workflow-tomorrow`` options.',
                'default': None,
                'metavar': 'FILE'
            },
            'wow-options': {
                'help': 'Additional options for workflow-tomorrow'
            },
            'use-general-test-plan': {
                'help': 'Use general test plan for available build identified from primary_task shared function.',
                'action': 'store_true'
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

    def beaker_job_xml(self, options=None, environment=None, task_params=None):
        """
        Run workflow-tomorrow to create beaker job XML.

        It does not take care about any SUT installation, it's up to the caller to provide
        necessary options.

        ``workflow-tomorrow`` options are selected from ``wow-options-map`` based on what rules are
        matched given the context this modules is running in (distros, arches, and so on).

        Caller can control what environmental variables are passed to his tasks with ``task_params`` parameter.
        Each `key/value` pair is passed to ``workflow-tomorrow`` via ``--taskparam="<key>=<value>"`` option.

        :param list options: additional options for ``workflow-tomorrow``.
        :param dict environment: if set, it will be passed to the tests via ``--environment`` option.
        :param dict task_params: if set, params will be passed to the tests via multiple
            ``--taskparam`` options.
        :returns: List of elements representing Beaker jobs designed by ``workflow-tomorrow``, one
            for each distro.
        """

        # pylint: disable=too-many-statements

        self.info('running workflow-tomorrow to get job description')

        self.require_shared('distro', 'evaluate_instructions', 'tasks', 'primary_task')

        primary_task = self.shared('primary_task')

        if not self.option('wow-options') and not self.option('use-general-test-plan'):
            raise NoTestAvailableError(primary_task)

        def _plan_job(distro):
            # pylint: disable=too-many-statements

            self.debug("constructing options distro '{}'".format(distro))

            context = gluetool.utils.dict_update(self.shared('eval_context'), {
                'DISTRO': distro
            })

            command = WowCommand(['bkr', 'workflow-tomorrow'], [
                '--dry',  # this will make wow to print job description in XML
                '--decision'  # show desicions about including/not including task in the job
            ] + (options or []), logger=self.logger)

            instruction_commands = {
                'add-options': command.add_options,
                'command': command.set_command,
                'use-shell': command.set_use_shell,
                'quote-args': command.set_quote_args
            }

            self.shared('evaluate_instructions', self.wow_options_map, instruction_commands, context=context)

            #
            # add options specified on command-line
            if self.option('wow-options'):
                command.options += shlex.split(self.option('wow-options'))

            #
            # add environment if available
            _environment = {}

            if self.has_shared('product'):
                _environment['product'] = self.shared('product')

            # incorporate changes demanded by user
            _environment.update(environment or {})

            command.options += [
                '--environment',
                ' && '.join(['{}={}'.format(k, v) for k, v in _environment.iteritems()])
            ] if _environment else []

            # incorporate changes demanded by user
            if task_params:
                for name, value in task_params.iteritems():
                    command.options += ['--taskparam="{}={}"'.format(name, value)]

            # incorporate general test plan if requested
            if self.option('use-general-test-plan'):
                component = primary_task.component

                try:
                    command.options += ['--plan={}'.format(str(qe.GeneralPlan(component).id))]

                except qe.GeneralPlanError:
                    raise NoGeneralTestPlanError(primary_task)

            try:
                output = command.run()

                return bs4.BeautifulSoup(output.stdout, 'xml')

            except GlueCommandError as exc:
                # Check for most common causes, and raise soft error where necessary
                if 'No relevant tasks found in test plan' in exc.output.stderr:
                    raise NoTestAvailableError(primary_task)

                if 'No recipe generated (no relevant tasks?)' in exc.output.stderr:
                    raise NoTestAvailableError(primary_task)

                raise GeneralWOWError(primary_task, exc.output)

        # For each distro, construct one wow command/job
        return [_plan_job(distro) for distro in self.shared('distro')]
