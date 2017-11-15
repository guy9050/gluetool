import re
import shlex

import gluetool
from gluetool import GlueError, SoftGlueError, GlueCommandError


class NoTestAvailableError(SoftGlueError):
    def __init__(self):
        super(NoTestAvailableError, self).__init__('No tests provided for the component')


class SclRun(gluetool.Module):
    name = 'sclrun'
    description = 'Uses sclrun (layer above workflow-tomorrow) to create beaker job XML description.'

    options = {
        'wow-options': {
            'help': 'Additional options for workflow-tomorrow'
        }
    }

    shared_functions = ['beaker_job_xml']

    def beaker_job_xml(self, options=None, environment=None, task_params=None, setup_phases=None):
        """
        Run sclrun to create beaker job XML.

        It does not take care about any SUT installation, it's up to the caller to provide
        necessary options.

        Caller can control what environmental variables are passed to his tasks with ``task_params`` parameter.
        Each `key/value` pair is passed to ``workflow-tomorrow`` via ``--taskparam="<key>=<value>"`` option. By
        default, ``beaker_job_xml`` adds following variables:

        * ``BASEOS_CI=true``
        * ``BASEOS_CI_TASKS=<comma-separated list of Brew/Koji tasks installed on the box>``
        * ``BASEOS_CI_COMPONENT=<component name>``
        * ``BASEOS_CI_TASK_TARGET=<build target if the primary task>``
        * ``BEAKERLIB_RPM_DOWNLOAD_METHODS='yum direct'``

        To override any of these variables, simply pass your own value in ``task_params`` parameter.

        :param list options: additional options for sclrun (or workflow-tomorrow).
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

        self.info('running sclrun to get job description')

        options = options or []
        environment = environment or {}
        task_params = task_params or {}

        self.require_shared('tasks', 'primary_task')

        primary_task = self.shared('primary_task')

        #
        # setup phases
        if setup_phases is None:
            setup_phases = ['beakerlib']

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
        else:
            _environment['product'] = "rhscl"

        # incorporate changes demanded by user
        _environment.update(environment)

        options += [
            '--environment',
            ' && '.join(['{}={}'.format(k, v) for k, v in _environment.iteritems()])
        ] if _environment else []

        #
        # add distro if available
        # if self.has_shared('distro'):
        #    options += ['--distro', self.shared('distro')]

        #
        # add global task parameters
        _task_params = {
            'BASEOS_CI': 'true',
            'BEAKERLIB_RPM_DOWNLOAD_METHODS': 'yum\\ direct',
            'BASEOS_CI_TASKS': ','.join([str(task.task_id) for task in self.shared('tasks')]),
            'BASEOS_CI_COMPONENT': str(primary_task.component),
            'BASEOS_CI_TASK_TARGET': str(self.shared('primary_task').target)
        }

        # incorporate changes demanded by user
        _task_params.update(task_params)

        for name, value in _task_params.iteritems():
            options += ['--taskparam', '{}={}'.format(name, value)]

        # detect collection name and rhel version from brew target
        scl = re.sub("rhscl-[^-]*-(.*)-rhel.*", "\\1", primary_task.target)
        distro = primary_task.rhel

        # add '"' to strings containing spaces to prevent bad expansion in sclrun
        options = [('"{}"'.format(option) if ' ' in option and not option.startswith('"') else option)
                   for option in options]

        #
        # construct command-line
        command = [
            'sclrun',
            '--dry',  # this will make sclrun to print job description in XML
            '--decision',  # show desicions about including/not including task in the job
            '--collection=' + scl,
            '--distro=' + distro
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

            raise GlueError("Failure during 'sclrun' execution: {}".format(exc.output.stderr))

    def sanity(self):
        if not self.option('wow-options'):
            raise NoTestAvailableError()
