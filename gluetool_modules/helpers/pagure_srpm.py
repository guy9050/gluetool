import os
import shlex
import gluetool
from gluetool.utils import Command
from gluetool_modules.libs.brew_build_fail import run_command


class PagureSRPM(gluetool.Module):

    name = 'pagure-srpm'
    description = 'Makes source rpm from pagure pull request'

    options = {
        'git-clone-options': {
            'help': 'Additional options for `git clone` command (default: %(default)s).',
            'default': ''
        },
        'git-fetch-options': {
            'help': 'Additional options for `git fetch` command (default: %(default)s).',
            'default': ''
        },
        'git-merge-options': {
            'help': 'Additional options for `git merge` command (default: %(default)s).',
            'default': ''
        },
    }

    shared_functions = ['src_rpm']

    def src_rpm(self):
        self.require_shared('primary_task')

        pull_request = self.shared('primary_task')

        if pull_request.ARTIFACT_NAMESPACE not in ['dist-git-pr']:
            raise gluetool.GlueError('Incompatible artifact namespace: {}'.format(pull_request.ARTIFACT_NAMESPACE))

        clone_cmd = [
            'git', 'clone',
            '-b', pull_request.destination_branch,
            pull_request.project.clone_url
        ]

        if self.option('git-clone-options'):
            clone_cmd.extend(shlex.split(self.option('git-clone-options')))
        run_command(
            self,
            Command(clone_cmd, logger=self.logger),
            'Clone git repository'
        )

        os.chdir(pull_request.project.name)

        pr_id = pull_request.pull_request_id.repository_pr_id

        fetch_cmd = ['git', 'fetch', 'origin', 'refs/pull/{}/head'.format(pr_id)]

        if self.option('git-fetch-options'):
            fetch_cmd.extend(shlex.split(self.option('git-fetch-options')))

        run_command(
            self,
            Command(fetch_cmd, logger=self.logger),
            'Fetch pull request changes'
        )

        merge_cmd = ['git', 'merge', 'FETCH_HEAD', '-m', 'ci pr merge']

        if self.option('git-merge-options'):
            merge_cmd.extend(shlex.split(self.option('git-merge-options')))

        run_command(
            self,
            Command(merge_cmd, logger=self.logger),
            'merge pull request changes'
        )

        last_comment_id = pull_request.comments[-1]['id'] if pull_request.comments else 0

        spec_origin_name = '{}.spec'.format(pull_request.project.name)
        spec_backup_name = '{}.backup'.format(spec_origin_name)

        os.rename(spec_origin_name, spec_backup_name)

        with open(spec_backup_name, 'r') as infile, open(spec_origin_name, 'w') as outfile:
            for line in infile.readlines():
                line = line.replace('%{?dist}', '.0.pr.{}.c.{}%{{?dist}}'.format(pull_request.uid, last_comment_id))
                outfile.writelines(line)

        rhpkg_cmd = ['rhpkg', 'srpm']
        output = run_command(
            self,
            Command(rhpkg_cmd, logger=self.logger),
            'Make srpm'
        )

        src_rpm_name = output.stdout.split('/')[-1].strip()

        return src_rpm_name
