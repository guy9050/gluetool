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


class WorkflowTomorrow(libci.Module):
    name = 'wow'
    description = 'Uses workflow-tomorrow to create beaker job XML description.'

    options = {
        'wow-options': {
            'help': 'Additional options for workflow-tomorrow'
        }
    }

    shared_functions = ['beaker_job_xml']

    def beaker_job_xml(self, options=None, environment=None, task_params=None):
        """
        Run workflow-tomorrow to create beaker job XML.

        It does not take care about any SUT installation, it's up to the caller to provide
        necessary options.

        :param list options: additional options for workflow-tomorrow.
        :param dict environment: if set, it will be passed to the tests via ``--environment``
            option.
        :param dict task_params: if set, params will be passed to the tests via multiple
            ``--taskparam`` options.
        :returns: libci.utils.ProcessOutput with the output of w-t.
        """

        self.info('running workflow-tomorrow to get job description')

        options = options or []
        environment = environment or {}
        task_params = task_params or {}

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
        if self.has_shared('distro'):
            options += ['--distro', self.shared('distro')]

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
            return libci.utils.run_command(command)

        except CICommandError as exc:
            # Check for most common causes, and raise soft error where necessary
            if 'No relevant tasks found in test plan' in exc.output.stderr:
                raise NoTestAvailableError()

            if 'No recipe generated (no relevant tasks?)' in exc.output.stderr:
                raise NoTestAvailableError()

            raise CIError("Failure during 'wow' execution: {}".format(exc.output.stderr))

    def sanity(self):
        if not self.option('wow-options'):
            raise NoTestAvailableError()