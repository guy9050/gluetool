import json
import re
import time
import os
import sys
import logging
import subprocess
from libci import Module
from libci import CIError, CICommandError
from libci import utils
from libci.utils import run_command


# TODO: move to config
GIT_BASE_URL = "git://pkgs.devel.redhat.com/rpms/"

class CIBuildOnCommit(Module):
    """
    CI Build-on-commit module

    This module schedules an scratch build on commit in either staging branch or
    in branch containing ci.yaml.
    """

    name = 'build-on-commit'
    description = 'Schedule scratch build for given component and branch'

    options = {
        'blacklist': {
            'help': 'A comma seaparted list of blacklisted package names',
        },
        'component': {
            'help': 'Component name',
        },
        'branch': {
            'help': 'Git branch',
        # TODO: add required target (consult with mhlavinka)
        }

    }
    required_options = ['component', 'branch']

    @staticmethod
    def _run_command(command):
        try:
            return run_command(command)

        except CICommandError as exc:
            raise CIError("Failure during 'boc' execution: {}".format(exc.output.stderr))


    def _build_package(self, target):
        cwd = os.getcwd()
        #self.info("cwd = {0}".format(cwd))
        git_dir = cwd + '/' + self.component
        if not os.path.isdir(git_dir):
            command = ["git", "clone", GIT_BASE_URL + self.component]
            CIBuildOnCommit._run_command(command)
        os.chdir(git_dir)
        command = ["rhpkg", "switch-branch", self.branch]
        CIBuildOnCommit._run_command(command)

        
        # scheduling scratch build
        command = ["rhpkg", "build", "--scratch", "--skip-nvr-check", "--arches",
                                            "x86_64", "--target", target]
        self.info("scheduling scratch build of component '{0}' and branch '{1}'".format(self.component, self.branch))
        # dry mod: just print the command, don't execute it
        #self.info("Running ing dry mode (testing), no scratch build will be scheduled")
        self.info(command)
        run_command(command, stdout = 1, stderr = 1)

        os.chdir(cwd)
        # cleanup
        # TODO: this can be removed when there is shared working dir (config)
        command = ["rm", '-rf', self.component]
        CIBuildOnCommit._run_command(command)

        return

    def is_staging(self):
        return "staging" in self.branch

    def execute(self):
        self.component = self.option('component')
        self.branch = self.option('branch')
        blacklist = self.option('blacklist')
        #self.info("Build-on-commit for component '{0}' and branch '{1}'.".format(self.component, self.branch))

        if not self.is_staging():
            self.error("Branch '{0}' is not staging branch, skipping execution".format(self.branch))
            raise CIError("Trying to schedule build for non-staging branch")

        # TODO: blacklist

        # TODO: read targets from ci.yaml ?
        if "rhel-7" in self.branch:
            target = "staging-rhel-7-candidate"
        elif "rhel-6" in self.branch:
            target = "staging-rhel-6-candidate"
        else:
            # this can be provided in configuration of boc module or via ci.yaml in repo
            raise CIError("Failure during 'boc': failed to detect rhel version from branch {}".format(self.branch))

        # schedule scratch build
        self._build_package(target)

