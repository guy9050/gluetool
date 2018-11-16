import collections

import gluetool
from gluetool import GlueError
from gluetool.utils import cached_property, PatternMap, render_template


DistGitRepository = collections.namedtuple('DistGitRepository', ('url', 'branch', 'package'))


class DistGit(gluetool.Module):
    """
    Module provides access to the dist-git repository of an artifact. The repository is made available via the shared
    function ```dist_git_repository```, which returns a namedtuple DistGitRepository('url', 'branch', 'package').
    """

    name = 'dist-git'
    description = 'Provide dist-git repository for an artifact.'

    options = {
        'branch-map': {
            'help': 'Path to a pattern map for mapping artifact target to dist-git branch'
        },
        'repository-map': {
            'help': 'Path to a pattern map for mapping artifact type to dist-git repositories'
        },
    }

    required_options = ('branch-map', 'repository-map')
    shared_functions = ['dist_git_repository']

    @cached_property
    def branch_map(self):
        return PatternMap(self.option('branch-map'), logger=self.logger)

    @cached_property
    def repository_map(self):
        return PatternMap(self.option('repository-map'), logger=self.logger)

    def dist_git_repository(self, branch=None, task=None):
        """
        Returns a dist-git repository in the form of a named tuple DistGitRepository('url', 'branch', 'package').

        :param str branch: Branch to use.
        :param object task: Task to use for mapping, provided by primary_task or tasks shared function.
        """

        # load task from primary_task if not specified
        if not task:
            self.require_shared('primary_task')
            task = self.shared('primary_task')

        # map artifact's target to branch name
        branch = branch or self.branch_map.match(task.target)

        if branch is None:
            raise GlueError("Could not translate target to dist-git branch")

        branch = render_template(branch, **self.shared('eval_context'))

        # map repository according to artifact's namespace
        repository = self.repository_map.match(task.ARTIFACT_NAMESPACE)

        if repository is None:
            raise GlueError("Could not translate target to repository")

        url = render_template(repository, **self.shared('eval_context'))
        self.info("dist-git repository URL is '{}' branch '{}'".format(url, branch))

        return DistGitRepository(url=url, branch=branch, package=task.component)
