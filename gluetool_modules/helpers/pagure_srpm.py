import os
import gluetool
from gluetool.utils import Command


class PagureSRPM(gluetool.Module):

    name = 'pagure-srpm'
    description = 'Makes source rpm from pagure pull request'

    shared_functions = ['src_rpm']

    def src_rpm(self):
        self.require_shared('primary_task')

        pull_request = self.shared('primary_task')

        if pull_request.ARTIFACT_NAMESPACE not in ['dist-git-pr']:
            raise gluetool.GlueError('Incompatible artifact namespace: {}'.format(pull_request.ARTIFACT_NAMESPACE))

        git_clone_cmd = [
            'git', 'clone',
            '-b', pull_request.destination_branch,
            pull_request.project.clone_url
        ]
        Command(git_clone_cmd, logger=self.logger).run()

        os.chdir(pull_request.project.name)

        pr_id = pull_request.pull_request_id.repository_pr_id

        fetch_cmd = ['git', 'fetch', 'origin', 'refs/pull/{}/head'.format(pr_id)]
        Command(fetch_cmd, logger=self.logger).run()

        merge_cmd = ['git', 'merge', 'FETCH_HEAD', '-m', 'ci pr merge']
        Command(merge_cmd, logger=self.logger).run()

        last_comment_id = pull_request.comments[-1]['id'] if pull_request.comments else 0

        spec_origin_name = '{}.spec'.format(pull_request.project.name)
        spec_backup_name = '{}.backup'.format(spec_origin_name)

        os.rename(spec_origin_name, spec_backup_name)

        with open(spec_backup_name, 'r') as infile, open(spec_origin_name, 'w') as outfile:
            for line in infile.readlines():
                line = line.replace('%{?dist}', '%{{?dist}}.pr.{}.c.{}'.format(pr_id, last_comment_id))
                outfile.writelines(line)

        command = ['rhpkg', 'srpm']
        output = Command(command).run()

        src_rpm_name = output.stdout.split('/')[-1].strip()

        return src_rpm_name
