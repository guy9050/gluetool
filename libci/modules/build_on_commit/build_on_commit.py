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
        },
        'shared-dir': {
            'help': 'Shared directory used for cloning repositories',
        }

    }
    required_options = ['component', 'branch', 'shared-dir', 'branch-pattern']

    def sanity(self):
        """
        Make sure that rhpkg tool is available.

        Make sure that shared directory exists as early as possible
        and change the current working directory to it.
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

        # create jenkins build label
        if self.shared('jenkins') and os.getenv('BUILD_URL', None):
            label = component + ": " + branch
            self.info("setting jenkins build name to \"{0}\"".format(label))
            self.shared('jenkins').set_build_name(label)

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

        # create shared directory if needed
        shared_dir = self.option('shared-dir')
        if not os.path.isdir(shared_dir):
            os.mkdir(shared_dir)
        os.chdir(shared_dir)

        # update existing repository or clone a new one
        if os.path.isdir(component):
            os.chdir(component)
            self.info("updating existing repository of '{}'".format(component))
            command = ["rhpkg", "switch-branch", "--fetch", branch]
            self._run_command(command)
        else:
            self.info("cloning repository of '{}'".format(component))
            command = ["git", "clone", GIT_BASE_URL + component]
            self._run_command(command)
            os.chdir(component)

        # schedule scratch build
        msg = ['scheduling scratch build of component']
        msg += ["'{}' on branch '{}' with build target '{}'".format(component, branch, target)]
        self.info(' '.join(msg))
        command = ["rhpkg", "build", "--scratch", "--skip-nvr-check", "--arches", "x86_64", "--target", target]
        self._run_command(command)
