import shlex
import qe

import gluetool
from gluetool import GlueError, SoftGlueError, GlueCommandError


class NoTestAvailableError(SoftGlueError):
    def __init__(self):
        super(NoTestAvailableError, self).__init__('No tests provided for the component')


class WorkflowTomorrow(gluetool.Module):
    name = 'wow'
    description = 'Uses workflow-tomorrow to create beaker job XML description.'

    options = [
        ('Global options', {
            'wow-options': {
                'help': 'Additional options for workflow-tomorrow'
            },
            'use-general-test-plan': {
                'help': 'Use general test plan for available build identified from primary_task shared function.',
                'action': 'store_true'
            }
        }),
        ('Tweak options', {
            'default-setup-phases': {
                'help': 'Comma-separated list of arguments for ``--setup`` option.',
                'default': '',
                'type': str
            }
        })
    ]

    shared_functions = ['beaker_job_xml']

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    def beaker_job_xml(self, options=None, environment=None, task_params=None, setup_phases=None):
        """
        Run workflow-tomorrow to create beaker job XML.

        It does not take care about any SUT installation, it's up to the caller to provide
        necessary options.

        Caller can control what environmental variables are passed to his tasks with ``task_params`` parameter.
        Each `key/value` pair is passed to ``workflow-tomorrow`` via ``--taskparam="<key>=<value>"`` option. By
        default, ``beaker_job_xml`` adds following variables:

        * ``BASEOS_CI=true``
        * ``BASEOS_CI_COMPONENT=<component name>``
        * ``BASEOS_CI_TASKS=<comma-separated list of Brew/Koji tasks installed on the box>``
        * ``BASEOS_CI_TASK_TARGET=<build target if the primary task>``
        * ``BEAKERLIB_RPM_DOWNLOAD_METHODS='yum direct'``

        To override any of these variables, simply pass your own value in ``task_params`` parameter.

        :param list options: additional options for workflow-tomorrow.
        :param dict environment: if set, it will be passed to the tests via ``--environment``
            option.
        :param dict task_params: if set, params will be passed to the tests via multiple
            ``--taskparam`` options.
        :param list setup_phases: if set, it's a list of valus which will be passed to
            ``workflow-tomorrow`` via multiple ``--setup`` options. If ``None`` is passed,
            ``['beakerlib']`` is used by default (if you don't want your job to use ``--setup=beakerlib``,
            use ``setup_phases=[]``).
        :returns: :py:class:`gluetool.utils.ProcessOutput` instance with the output of ``workflow-tomorrow``.
        """

        self.info('running workflow-tomorrow to get job description')

        if not self.option('wow-options') and not self.option('use-general-test-plan'):
            raise NoTestAvailableError()

        self.require_shared('tasks', 'primary_task')

        options = options or []
        environment = environment or {}
        task_params = task_params or {}

        #
        # setup phases
        if setup_phases is None:
            setup_phases = [
                phase.strip() for phase in self.option('default-setup-phases').strip().split(',')
            ]

        for phase in setup_phases:
            options += ['--setup', phase]

        #
        # add options specified on command-line
        if self.option('wow-options'):
            options += shlex.split(self.option('wow-options'))

        #
        # add environment if available
        _environment = {}

        if self.has_shared('product'):
            _environment['product'] = self.shared('product')

        # incorporate changes demanded by user
        _environment.update(environment)

        options += [
            '--environment',
            ' && '.join(['{}={}'.format(k, v) for k, v in _environment.iteritems()])
        ] if _environment else []

        #
        # add distro if available
        if self.has_shared('distro'):
            options += ['--distro', self.shared('distro')]

        #
        # add global task parameters
        _task_params = {
            'BASEOS_CI': 'true',
            'BEAKERLIB_RPM_DOWNLOAD_METHODS': 'yum\\ direct',
            'BASEOS_CI_TASKS': ','.join([str(task.task_id) for task in self.shared('tasks')]),
            'BASEOS_CI_COMPONENT': str(self.shared('primary_task').component),
            'BASEOS_CI_TASK_TARGET': str(self.shared('primary_task').target)
        }

        # incorporate changes demanded by user
        _task_params.update(task_params)

        for name, value in _task_params.iteritems():
            options += ['--taskparam', '{}={}'.format(name, value)]

        # incorporate general test plan if requested
        if self.option('use-general-test-plan'):
            component = self.shared('primary_task').component
            try:
                options += ['--plan', str(qe.GeneralPlan(component).id)]
            except qe.GeneralPlanError:
                raise GlueError("no general test plan found for '{}'".format(component))

        #
        # construct command-line
        command = [
            'bkr', 'workflow-tomorrow',
            '--dry',  # this will make wow to print job description in XML
            '--decision'  # show desicions about including/not including task in the job
        ] + options

        #
        # execute
        try:
            return gluetool.utils.run_command(command)

        except GlueCommandError as exc:
            # Check for most common causes, and raise soft error where necessary
            if 'No relevant tasks found in test plan' in exc.output.stderr:
                raise NoTestAvailableError()

            if 'No recipe generated (no relevant tasks?)' in exc.output.stderr:
                raise NoTestAvailableError()

            raise GlueError("Failure during 'wow' execution: {}".format(exc.output.stderr))
