import shlex

import bs4
import jinja2
import qe

import gluetool
from gluetool import GlueError, SoftGlueError, GlueCommandError
from libci.sentry import PrimaryTaskFingerprintsMixin


class NoTestAvailableError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task):
        super(NoTestAvailableError, self).__init__(task, 'No tests provided for the component')


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

        self.info('running workflow-tomorrow to get job description')

        self.require_shared('distro', 'evaluate_rules', 'tasks', 'primary_task')

        if not self.option('wow-options') and not self.option('use-general-test-plan'):
            raise NoTestAvailableError(self.shared('primary_task'))

        options = options or []
        environment = environment or {}
        task_params = task_params or {}

        def _plan_job(distro):
            command = [
                'bkr', 'workflow-tomorrow',
                '--dry',  # this will make wow to print job description in XML
                '--decision'  # show desicions about including/not including task in the job
            ] + options

            self.debug("constructing options distro '{}'".format(distro))

            rules_context = {
                'BUILD_TARGET': self.shared('primary_task').target,
                'PRIMARY_TASK': self.shared('primary_task'),
                'TASKS': self.shared('tasks'),
                'DISTRO': distro
            }

            # Options set by a configuration
            for options_set in self.wow_options_map:
                gluetool.log.log_dict(self.debug, 'options set', options_set)

                if not self.shared('evaluate_rules', options_set.get('rule', 'False'), context=rules_context):
                    self.debug('rule does not match, moving on')
                    continue

                if 'add-options' in options_set:
                    add_options = jinja2.Template(options_set['add-options']).render(**custom_locals)
                    gluetool.log.log_blob(self.debug, 'adding options', add_options)

                    # simple split() is too dumb: '--foo "bar baz"' => ['--foo', 'bar baz']. shlex is the right tool
                    # to split command-line options, it obeys quoting.
                    command += shlex.split(add_options)

            #
            # add options specified on command-line
            if self.option('wow-options'):
                command += shlex.split(self.option('wow-options'))

            #
            # add environment if available
            _environment = {}

            if self.has_shared('product'):
                _environment['product'] = self.shared('product')

            # incorporate changes demanded by user
            _environment.update(environment)

            command += [
                '--environment',
                ' && '.join(['{}={}'.format(k, v) for k, v in _environment.iteritems()])
            ] if _environment else []

            # incorporate changes demanded by user
            for name, value in task_params.iteritems():
                command += ['--taskparam', '{}={}'.format(name, value)]

            # incorporate general test plan if requested
            if self.option('use-general-test-plan'):
                component = self.shared('primary_task').component
                try:
                    command += ['--plan', str(qe.GeneralPlan(component).id)]

                except qe.GeneralPlanError:
                    raise GlueError("no general test plan found for '{}'".format(component))

            #
            # execute
            try:
                output = gluetool.utils.run_command(command)

                return bs4.BeautifulSoup(output.stdout, 'xml')

            except GlueCommandError as exc:
                # Check for most common causes, and raise soft error where necessary
                if 'No relevant tasks found in test plan' in exc.output.stderr:
                    raise NoTestAvailableError(self.shared('primary_task'))

                if 'No recipe generated (no relevant tasks?)' in exc.output.stderr:
                    raise NoTestAvailableError(self.shared('primary_task'))

                raise GlueError("Failure during 'wow' execution: {}".format(exc.output.stderr))

        # For each distro, construct one wow command/job
        return [_plan_job(distro) for distro in self.shared('distro')]
