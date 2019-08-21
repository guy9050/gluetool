import os
import re
import shutil

import gluetool
from gluetool import GlueCommandError, GlueError, SoftGlueError
from gluetool.utils import cached_property, check_for_commands, Command, PatternMap

# Required commands
# rhpkg, rpmbuild - for building scratch build
# brew - to wait for package build
REQUIRED_CMDS = ['rhpkg', 'brew', 'rpmbuild']


class BocBuildError(SoftGlueError):
    def __init__(self, branch, component, target, task_url):
        super(BocBuildError, self).__init__('Build failed')

        self.branch = branch
        self.component = component
        self.target = target
        self.task_url = task_url


class BocNoBranchError(SoftGlueError):
    def __init__(self, branch, component):
        super(BocNoBranchError, self).__init__('Branch {} not found in git for {}'.format(branch, component))

        self.branch = branch
        self.component = component


class CIBuildOnCommit(gluetool.Module):
    """
    Schedule a Brew scratch build of given component and branch. As build target use the mapping
    value specified in the mapping file.

    The module first clones the dist-git repository of the specified component. The cloned repository
    is removed in the destroy function.

    Optionally it is possible to blacklist components.
    """

    name = 'build-on-commit'
    description = 'Schedule scratch Brew build for given component and branch.'

    options = {
        'blacklist': {
            'help': 'A comma separated list of blacklisted package names.',
        },
        'branch': {
            'help': 'Git branch of repository to build.',
        },
        'component': {
            'help': 'Component name.',
        },
        'git-base-url': {
            'help': 'Dist-git base URL used for cloning.'
        },
        'pattern-map': {
            'help': 'Path to file with branch => build target patterns. Build will be built only in case of a match.'
        },
    }
    required_options = ['branch', 'git-base-url', 'component', 'pattern-map']

    def __init__(self, *args, **kwargs):
        super(CIBuildOnCommit, self).__init__(*args, **kwargs)

        self.branch = None
        self.component = None
        self.target = None
        self.task_url = None

    def sanity(self):
        """
        Checks that required commands are available on the host.
        """
        check_for_commands(REQUIRED_CMDS)

    def set_build_name(self, label):
        """
        Use Jenkins REST API to change build name.
        """
        if not self.require_shared('jenkins', warn_only=True):
            return

        build_url = os.getenv('BUILD_URL', None)
        if build_url is None:
            self.warn('$BUILD_URL env var not found, was this job started by Jenkins?', sentry=True)
            return

        self.shared('jenkins').set_build_name(label)
        self.info("build name set: '{}'".format(label))

    @cached_property
    def pattern_map(self):
        """ returns PatternMap instance from the mapping file """
        return PatternMap(self.option('pattern-map'), logger=self.logger)

    def destroy(self, failure=None):
        if self.component and os.path.exists(self.component):
            self.info("removing cloned git repository '{}'".format(self.component))
            shutil.rmtree(self.component)

    def execute(self):
        self.component = component = self.option('component')
        self.branch = branch = self.option('branch')
        blacklist = self.option('blacklist')

        # set jenkins build name
        label = component + ": " + branch
        self.set_build_name(label)

        # blacklist packages
        if blacklist:
            self.verbose('blacklisted packages: {}'.format(blacklist))
            if component in [s.strip() for s in blacklist.split(',')]:
                self.info('skipping blacklisted component {}'.format(component))
                return

        # try to map the branch to build target
        self.target = target = self.pattern_map.match(branch)
        if target is None:
            raise GlueError("failed to detect build-target from branch '{}'".format(branch))
        self.info("for branch '{}' using build target '{}'".format(branch, target))

        #  create shallow clone of git repo, just 1 branch, no history
        self.info("cloning repository of '{}', branch '{}'".format(component, branch))
        git_args = ["--depth", "1", "--single-branch", "--branch", branch]
        command = ["git", "clone", os.path.join(self.option('git-base-url'), component)] + git_args
        try:
            Command(command).run()
        except GlueCommandError as exc:
            if "Remote branch {} not found in upstream origin".format(self.branch) in exc.output.stderr:
                raise BocNoBranchError(self.branch, self.component)
            raise exc

        # schedule scratch build
        msg = ['scheduling scratch build of component']
        msg += ["'{}' on branch '{}' with build target '{}'".format(component, branch, target)]
        self.info(' '.join(msg))
        command = [
            "rhpkg", "--path", component, "build", "--scratch", "--skip-nvr-check", "--arches", "x86_64",
            "--target", target, "--nowait"
        ]

        output = Command(command).run()

        # detect brew task id
        taskid = re.search(".*Created task: [0-9]+", output.stdout, re.M).group()
        taskid = re.sub('^[^0-9]*([0-9]+)[0-9]*$', '\\1', taskid)

        # detect brew task URL and log it
        self.task_url = task_url = re.search(".*Task info:.*", output.stdout, re.M).group()
        task_url = re.sub('Task info: ', '', task_url)
        self.info("Waiting for brew to finish task: {0}".format(task_url))

        # if self.has_shared('report_pipeline_state'):
        #    self.shared('report_pipeline_state', 'started', artifact={
        #        'id': taskid,
        #        'namespace': 'brew',
        #        'branch': self.branch,
        #        'scratch': True
        #    })

        # wait until brew task finish
        command = ["brew", "watch-task", taskid]
        try:
            Command(command).run()
        except GlueCommandError as exc:
            raise BocBuildError(self.branch, self.component, self.target, self.task_url)

        # if self.has_shared('report_pipeline_state'):
        #    self.shared('report_pipeline_state', 'finished', artifact={
        #        'id': taskid,
        #        'namespace': 'brew',
        #        'branch': self.branch,
        #        'scratch': True
        #    })
