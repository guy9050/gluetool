import collections

import gluetool
from gluetool import GlueError
from gluetool.utils import cached_property, PatternMap, render_template, IncompatibleOptionsError


DistGitRepository = collections.namedtuple('DistGitRepository', ('url', 'branch', 'package'))


class DistGit(gluetool.Module):
    """
    Module provides details of a dist-git repository. The repository is made available via the shared
    function ```dist_git_repository```, which returns a namedtuple DistGitRepository('url', 'branch', 'package').

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
            'repository': {
                'help': 'Dist-git repository url'
            },
        }),
    ]

    required_options = ('method',)
    shared_functions = ['dist_git_repository']

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

    def dist_git_repository(self, branch=None, task=None):
        """
        Returns a dist-git repository in the form of a named tuple DistGitRepository('url', 'branch', 'package').

        :param str branch: Use given branch instead.
        :param object task: Use given task for mapping. By default the task will be provided by ``primary_task``
                            shared function.
        """

        # load task from primary_task if not specified
        if not task:
            self.require_shared('primary_task')
            task = self.shared('primary_task')

        # map artifact's target to branch name
        branch = branch or self._methods_branch[self.option('method')](self, task)

        if branch is None:
            raise GlueError("Could not translate target to dist-git branch or branch is empty")

        branch = render_template(branch, **self.shared('eval_context'))

        # map repository according to artifact's namespace
        repository = self._methods_repository[self.option('method')](self, task)

        if repository is None:
            raise GlueError("Could not translate target to dist-git repository or repository is empty")

        url = render_template(repository, **self.shared('eval_context'))
        self.info("dist-git repository URL is '{}' branch '{}'".format(url, branch))

        return DistGitRepository(url=url, branch=branch, package=task.component)
