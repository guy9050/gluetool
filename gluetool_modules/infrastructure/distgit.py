import re

import gluetool

from gluetool.utils import cached_property, IncompatibleOptionsError, log_blob, PatternMap, render_template

import gluetool_modules.libs


class DistGitRepository(object):
    """
    Provides a dist-git repository.
    """

    def __init__(self, module, url, branch, package, distgit_ref=None):
        # pylint: disable=too-many-arguments
        self._module = module

        module.logger.connect(self)

        self.logger = module.logger

        self.branch = branch
        self.package = package
        self.url = url
        self.distgit_ref = distgit_ref

    def __repr__(self):
        return '<DistGitRepository(package="{}", branch="{}")>'.format(self.package, self.branch)

    @cached_property
    def gating_config_url(self):
        return '{}/raw/{}/f/gating.yaml'.format(self.url, self.branch)

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

    The module supports two methods for resolving the dist-git repository details:

    * ``artifact``: Resolved dist-git repository for the primary artifact in the pipeline.

    * ``force``: Force repository and branch from the command line.
    """

    name = 'dist-git'
    description = 'Provide dist-git repository for an artifact.'
    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    options = [
        ('General options', {
            'method': {
                'help': 'What method to use for resolving dist-git repository (default: %(default)s).',
                'choices': ('artifact', 'force'),
                'default': 'artifact'
            },
        }),
        ("Options for method 'artifact'", {
            'branch-map': {
                'help': 'Path to a pattern map for mapping artifact target to dist-git branch'
            },
            'repository-map': {
                'help': 'Path to a pattern map for mapping artifact type to dist-git repositories'
            }
        }),
        ("Options for method 'force'", {
            'branch': {
                'help': 'Dist-git branch'
            },
            'distgit_ref': {
                'help': 'Dist-git ref'
            },
            'repository': {
                'help': 'Dist-git repository url'
            },
        }),
    ]

    required_options = ('method',)
    shared_functions = ['dist_git_repository']

    def __init__(self, *args, **kwargs):
        super(DistGit, self).__init__(*args, **kwargs)

        self._repository = None

    @property
    def eval_context(self):
        # pylint: disable=unused-variable
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
    def repository_map(self):
        return PatternMap(self.option('repository-map'), logger=self.logger)

    def _artifact_repository(self, task):
        return self.repository_map.match(task.ARTIFACT_NAMESPACE)

    def _artifact_branch(self, task):
        return self.branch_map.match(task.target)

    # pylint: disable=unused-argument
    def _force_branch(self, *args):
        return self.option('branch')

    # pylint: disable=unused-argument
    def _force_repository(self, *args):
        return self.option('repository')

    _methods_branch = {
        'artifact': _artifact_branch,
        'force': _force_branch
    }

    _methods_repository = {
        'artifact': _artifact_repository,
        'force': _force_repository
    }

    def sanity(self):
        method = self.option('method')
        artifact_options = ['branch-map', 'repository-map']
        force_options = ['branch', 'repository']

        if method == 'artifact' and not all([self.option(option) for option in artifact_options]):
            raise IncompatibleOptionsError("missing required options for method 'artifact'")

        if method == 'force' and not all([self.option(option) for option in force_options]):
            raise IncompatibleOptionsError("missing required options for method 'force'")

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

    def execute(self):
        self.require_shared('primary_task')
        task = self.shared('primary_task')

        # map artifact's target to branch name
        branch = self._methods_branch[self.option('method')](self, task)

        if branch is None:
            raise gluetool.GlueError('Could not translate target to dist-git branch or branch is empty')

        branch = render_template(branch, **self.shared('eval_context'))

        git_ref = self.option('distgit_ref')
        if not git_ref:
            git_ref = task.distgit_ref

        # map repository according to artifact's namespace
        repository = self._methods_repository[self.option('method')](self, task)

        if repository is None:
            raise gluetool.GlueError("Could not translate target to dist-git repository or repository is empty")

        url = render_template(repository, **self.shared('eval_context'))
        self.info("dist-git repository URL is '{}' branch '{}'".format(url, branch))

        self._repository = DistGitRepository(self, url=url, branch=branch, package=task.component, distgit_ref=git_ref)
