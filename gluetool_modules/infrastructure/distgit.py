import re

import gluetool

from gluetool.log import LoggerMixin
from gluetool.utils import cached_property, IncompatibleOptionsError, log_blob, PatternMap, render_template

import gluetool_modules.libs
import gluetool_modules.libs.git


class DistGitRepository(LoggerMixin, gluetool_modules.libs.git.RemoteGitRepository):
    """
    Provides a dist-git repository.
    """

    def __init__(self, module, package, clone_url=None, branch=None, ref=None, web_url=None):
        super(DistGitRepository, self).__init__(module.logger, clone_url, branch=branch, ref=ref, web_url=web_url)

        self._module = module

        self.package = package

    def __repr__(self):
        return '<DistGitRepository(package="{}", branch="{}")>'.format(self.package, self.branch)

    @cached_property
    def ci_config_url(self):
        """
        URL of CI configuration entry point (``ci.fmf``).
        """

        # In the future, this must cover greater variety of options - FMF allows multiple
        # ways how to specify "/ci" node.
        return '{}/raw/{}/f/ci.fmf'.format(self.web_url, self.ref if self.ref else self.branch)

    @cached_property
    def sti_tests_url(self):
        """
        URL of STI tests.
        """

        # Currently we check only tests/ folder, which should be a pretty solid indication of STI tests.
        # The STI tests can be tests/tests*.yml, which is a bit hard to check via URL as we would need to parse html.
        return '{}/blob/{}/f/tests'.format(self.web_url, self.ref if self.ref else self.branch)

    @cached_property
    def gating_config_url(self):
        return '{}/raw/{}/f/gating.yaml'.format(self.web_url, self.ref if self.ref else self.branch)

    def _get_url(self, url, success_message, failure_message):
        with gluetool.utils.requests() as request:
            response = request.get(url)

        if response.status_code == 200:
            self.info(success_message)

            return response.text

        self.info(failure_message)

        return None

    @cached_property
    def ci_config(self):
        """
        CI configuration.

        .. note::

           Limited to a single file, ``ci.fmf`` - FMF allows different ways how to write such configuration,
           as of now there's a hard limit on simple ``ci.fmf`` or nothing.
        """

        return self._get_url(self.ci_config_url, 'contains CI configuration', 'does not contain CI configuration')

    @cached_property
    def _sti_tests_folder(self):
        """
        STI tests folder, not interesting for the user, so keeping internal.
        """

        return self._get_url(self.sti_tests_url, 'has STI tests', 'does not have STI tests')

    @cached_property
    def _gating_config_response(self):
        with gluetool.utils.requests() as request:
            response = request.get(self.gating_config_url)

        if response.status_code == 200:
            log_blob(self.info, "gating configuration '{}'".format(self.gating_config_url), response.content)

            return response

        self.info("dist-git repository has no gating.yaml '{}'".format(self.gating_config_url))

        return None

    @cached_property
    def has_ci_config(self):
        """
        :returns: ``True`` when dist-git repository contains CI configuration, ``False`` otherwise.
        """

        return bool(self.ci_config)

    @cached_property
    def has_sti_tests(self):
        """
        :returns: ``True`` when dist-git repository contains Standard Test Interface (STI) tests, ``False`` otherwise.
        """

        return bool(self._sti_tests_folder)

    @cached_property
    def has_gating(self):
        """
        :returns: True if dist-git repository has gating enabled, False otherwise
        """
        return bool(self._gating_config_response)

    @cached_property
    def gating_recipients(self):
        """
        Returns list of recipients specified in a comment in gating.yaml file as a list. Here
        is an example of gating yaml with the recipients in an comment:

        .. code-block:: yaml

           ---

           # recipients: batman, robin
           product_versions:
           - rhel-8
           decision_context: osci_compose_gate
           rules:
           - !PassingTestCaseRule {test_case_name: baseos-ci.brew-build.tier1.functional}

        :returns: List of recipients form gating.yaml provided via comment in the gating.yaml file.
        """
        response = self._gating_config_response

        if not response or 'recipients:' not in response.content:
            return []

        return [
            recipient.strip() for recipients in re.findall("recipients:.*", response.content, re.MULTILINE)
            for recipient in recipients.lstrip("recipients:").split(',')
        ]


class DistGit(gluetool.Module):
    """
    Module provides details of a dist-git repository. The repository is made available via the shared
    function ```dist_git_repository```, which returns an instance of py:class:`DistGitRepository` class.

    The module supports currently one method for resolving the dist-git repository details:

    * ``artifact``: Resolve dist-git repository for the primary artifact in the pipeline. If some of the options
                    ``branch``, ``ref``, ``web-url`` or ``clone-url`` are specified they override the resolved values.
    """

    name = 'dist-git'
    description = 'Provide dist-git repository for an artifact.'
    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    options = [
        ('General options', {
            'method': {
                'help': 'What method to use for resolving dist-git repository (default: %(default)s).',
                'choices': ('artifact',),
                'default': 'artifact'
            },
        }),
        ("Options for method 'artifact'", {
            'branch-map': {
                'help': 'Path to a pattern map for mapping artifact target to dist-git branch'
            },
            'clone-url-map': {
                'help': 'Path to a pattern map for mapping artifact type to dist-git repository clone URL'
            },
            'web-url-map': {
                'help': 'Path to a pattern map for mapping artifact type to dist-git repository web URL'
            },
            'branch': {
                'help': 'Force dist-git branch'
            },
            'ref': {
                'help': 'Force dist-git ref'
            },
            'clone-url': {
                'help': 'Force dist-git repository clone URL'
            },
            'web-url': {
                'help': 'Force dist-git repository web URL'
            }
        })
    ]

    required_options = ('method',)
    shared_functions = ['dist_git_repository']

    def __init__(self, *args, **kwargs):
        super(DistGit, self).__init__(*args, **kwargs)

        self._repository = None

    @property
    def eval_context(self):
        __content__ = {  # noqa
            'DIST_GIT_REPOSITORY': """
                                    Dist-git repository, represented as ``DistGitRepository`` instance.
                                    """,
        }

        if not self._repository or gluetool_modules.libs.is_recursion(__file__, 'eval_context'):
            return {}

        return {
            'DIST_GIT_REPOSITORY': self._repository,
        }

    @cached_property
    def branch_map(self):
        return PatternMap(self.option('branch-map'), logger=self.logger)

    @cached_property
    def clone_url_map(self):
        return PatternMap(self.option('clone-url-map'), logger=self.logger)

    @cached_property
    def web_url_map(self):
        return PatternMap(self.option('web-url-map'), logger=self.logger)

    def _artifact_branch(self, task):
        # if ref is specified, we cannot use also branch, conflict of both checked in sanity
        if self.option('ref'):
            return None

        return self.option('branch') or self.branch_map.match(task.target)

    def _artifact_ref(self, task):
        # if branch is specified, we cannot use also ref, conflict of both checked in sanity
        if self.option('branch'):
            return None

        return self.option('ref') or task.distgit_ref

    def _artifact_clone_url(self, task):
        return self.option('clone-url') or self.clone_url_map.match(task.ARTIFACT_NAMESPACE)

    def _artifact_web_url(self, task):
        return self.option('web-url') or self.web_url_map.match(task.ARTIFACT_NAMESPACE)

    _methods_branch = {
        'artifact': _artifact_branch,
    }

    _methods_ref = {
        'artifact': _artifact_ref,
    }

    _methods_clone_url = {
        'artifact': _artifact_clone_url,
    }

    _methods_web_url = {
        'artifact': _artifact_web_url,
    }

    def sanity(self):
        required_options = [
            ('branch-map', 'branch'),
            ('clone-url-map', 'clone-url'),
            ('web-url-map', 'web-url')
        ]

        if not all([self.option(option[0]) or self.option(option[1]) for option in required_options]):
            raise IncompatibleOptionsError("missing required options for method 'artifact'")

        if self.option('ref') and self.option('branch'):
            raise IncompatibleOptionsError("You can use only one of 'ref' or 'branch'")

    def dist_git_repository(self):
        """
        Returns a dist-git repository for the primary_task in the pipeline in the form of an instance
        of the py:class:`DistGitRepository` class. The branch or task can be forced via module parameters
        with the same name.

        The module currently holds only one dist-git repository and it caches it after the first retrieval
        in the execute funtion.

        :returns: instance of the :py:class:`DistGitRepository`
        """

        return self._repository

    def _acquire_param(self, name, error_message=None):
        """
        For a given repo parameter, pick one of its getter methods and return the value.

        :param str name: name of the repository parameter.
        :param str error_message: if set and the value of parameter is not provided by the getter,
            an exception with this message is raised.
        """

        getter = getattr(self, '_methods_{}'.format(name))[self.option('method')]

        value = getter(self, self.shared('primary_task'))

        if not value:
            if error_message:
                raise gluetool.GlueError(error_message)

            return None

        # Use the initial value as a template for the final value
        context = self.shared('eval_context')

        return render_template(value, **context)

    def execute(self):
        self.require_shared('primary_task')
        task = self.shared('primary_task')

        if not task:
            raise gluetool.GlueError('No task available, cannot continue')

        # Gather repository parameters. Some of them may be missing - ref and branch - because we can
        # use defaults (like `HEAD` and `master`), some are required. Selects correct getter, based on
        # the method.
        kwargs = {
            'clone_url': self._acquire_param('clone_url', error_message='Could not acquire dist-git clone URL'),
            'web_url': self._acquire_param('web_url', error_message='Could not acquire dist-git web URL'),
            'branch': self._acquire_param('branch'),
            'ref': self._acquire_param('ref')
        }

        self._repository = DistGitRepository(self, task.component, **kwargs)

        self.info("dist-git repository {}, branch {}, ref {}".format(
            self._repository.web_url,
            self._repository.branch if self._repository.branch else 'not specified',
            self._repository.ref if self._repository.ref else 'not specified'
        ))
