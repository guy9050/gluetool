import os
import re
from libci import CICommandError, CIError, Module
from libci.utils import check_for_commands, run_command

# Base URL of the dit-git repositories
GIT_BASE_URL = 'git://pkgs.devel.redhat.com/rpms/'
# This module requires rhpkg tool installed
REQUIRED_CMDS = ['rhpkg']


class CIBuildOnCommit(Module):
    """
    CI Build-on-commit module

    This module schedules a scratch build on commit to branches which match any
    pattern specified via branch_pattern option.

    The branches are translated into staging-rhel[67]-candidate build targets,
    which are then used to build the package. If the translation fails, the
    build will not happen and the module will fail.
    """

    name = 'build-on-commit'
    description = 'Schedule scratch build for given component and branch'

    options = {
        'blacklist': {
            'help': 'A comma seaparted list of blacklisted package names',
        },
        'branch': {
            'help': 'Git branch of repository to build',
        },
        'branch-pattern': {
            'help': 'A comma separated list of regexes, which define branches that will be built',
        },
        'component': {
            'help': 'Component name',
        }

    }
    required_options = ['component', 'branch', 'branch-pattern']

    def sanity(self):
        """
        Make sure that rhpkg tool is available.
        """
        check_for_commands(REQUIRED_CMDS)

    @staticmethod
    def _run_command(command):
        try:
            run_command(command)
        except CICommandError as exc:
            error = exc.output.stdout.rstrip("'\n") + exc.output.stderr.rstrip("'\n")
            raise CIError("failure during '{}' execution\n{}'".format(command[0], error))

    def execute(self):
        component = self.option('component')
        branch = self.option('branch')
        blacklist = self.option('blacklist')
        branch_pattern = self.option('branch-pattern')

        # blacklist packages
        if blacklist:
            self.verbose('blacklisted packages: {}'.format(blacklist))
            if component in [s.strip() for s in blacklist.split(',')]:
                self.info('skipping blacklisted component {}'.format(component))
                return

        # check if branch is enabled in branch_pattern list
        if not any(re.match(regex.strip(), branch) for regex in branch_pattern.split(',')):
            self.info("skipping branch because it did not match branch_patterns '{}'".format(branch_pattern))
            return

        # transform branch name to build target
        match = re.match('.*rhel-([67]).*', branch)
        if match is None:
            raise CIError("failed to detect build-target from branch '{}'".format(branch))
        target = "staging-rhel-{}-candidate".format(match.group(1))

        #  create shallow clone of git repo, just 1 branch, no history
        self.info("cloning repository of '{}'".format(component))
        git_args = ["--depth", "1", "--single-branch", "--branch", branch]
        command = ["git", "clone", GIT_BASE_URL + component] + git_args
        self._run_command(command)
        os.chdir(component)

        # schedule scratch build
        msg = ['scheduling scratch build of component']
        msg += ["'{}' on branch '{}' with build target '{}'".format(component, branch, target)]
        self.info(' '.join(msg))
        command = ["rhpkg", "build", "--scratch", "--skip-nvr-check", "--arches", "x86_64", "--target", target]
        self._run_command(command)
