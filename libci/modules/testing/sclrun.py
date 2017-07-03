import shlex

import libci
from libci import CIError, SoftCIError, CICommandError


class NoTestAvailableError(SoftCIError):
    SUBJECT = 'No tests found for component'
    BODY = """

CI could not find any suitable tests for the component. This can have many different causes, e.g.:

    * component's configuration is incomplete, it does not provide correct test plan with tests
      for the component, or
    * the test plan is provided but it's empty, or
    * the test plan is not empty but there are filters applied in the configuration, and the result
      is an empty set of tests.

Please, see the documentation on CI configuration and what is required to correctly enable CI for
a component ([1]), current configuration ([2]), and/or consult with component's QE how to resolve
this situation.

[1] https://wiki.test.redhat.com/BaseOs/Projects/CI/Documentation/UserHOWTO#AddthecomponenttoCI
[2] https://gitlab.cee.redhat.com/baseos-qe/citool-config/raw/production/brew-dispatcher.yaml
    """

    def __init__(self):
        super(NoTestAvailableError, self).__init__('No tests provided for the component')


class SclRun(libci.Module):
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
        * ``BASEOS_CI_COMPONENT=<component name>`` - available only when Brew task is available.
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
        :returns: :py:class:`libci.utils.ProcessOutput` instance with the output of ``workflow-tomorrow``.
        """

        self.info('running sclrun to get job description')

        options = options or []
        environment = environment or {}
        task_params = task_params or {}

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

        # incorporate changes demanded by user
        _environment.update(environment)

        options += [
            '--environment'
            ' && '.join(['{}={}'.format(k, v) for k, v in _environment.iteritems()])
        ] if _environment else []

        #
        # add distro if available
        # if self.has_shared('distro'):
        #    options += ['--distro', self.shared('distro')]

        #
        # add global task parameters
        _task_params = {
            'BASEOS_CI': 'true'
        }

        task = self.shared('brew_task')
        if task is None:
            self.warn('No brew task available, cannot add BASEOS_CI_COMPONENT task param')

        else:
            _task_params['BASEOS_CI_COMPONENT'] = str(task.component)

        # incorporate changes demanded by user
        _task_params.update(task_params)

        for name, value in _task_params.iteritems():
            options += ['--taskparam', '{}={}'.format(name, value)]

        # detect collection name and rhel version from brew target
        target = task.target
        scl = target.collection
        distro = target.rhel

        # add '"' to strings containing spaces to prevent bad expansion in sclrun
        for option in options:
            if ' ' in option:
                option = '"' + option + '"'

        #
        # construct command-line
        command = [
            'sclrun',
            '--dry',  # this will make sclrun to print job description in XML
            '--decision',  # show desicions about including/not including task in the job
            '--collection=' + scl,
            '--distro=' + distro,
            '--environment product=rhscl'   # propagate product for relevancy rules
        ] + options

        #
        # execute
        try:
            return libci.utils.run_command(command)

        except CICommandError as exc:
            # Check for most common causes, and raise soft error where necessary
            if 'No relevant tasks found in test plan' in exc.output.stderr:
                raise NoTestAvailableError()

            if 'No recipe generated (no relevant tasks?)' in exc.output.stderr:
                raise NoTestAvailableError()

            raise CIError("Failure during 'sclrun' execution: {}".format(exc.output.stderr))

    def sanity(self):
        if not self.option('wow-options'):
            raise NoTestAvailableError()
