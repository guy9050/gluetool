from libci import CIError, CICommandError, Module
from libci.utils import check_for_commands, run_command, load_yaml

REQUIRED_CMDS = ['brew']
TEST_TYPES = ['wow', 'restraint']


class CIBrewTagBuild(Module):
    """
    Tags brew builds with given tag which match the given build target and overall result is passed.
    The mappings are configured via a yaml configuration file.

    Below is an example of the yaml configuration file.

    .. code-block:: yaml

       ---
       # specifies the tag group
       tier1:
         - rhel-7.4-candidate: rhel-7.4-tier1
         - rhel-6.9-candidate: rhel-6.9-tier1
    """

    name = 'brew-tag-build'
    config = None

    options = {
        'config': {
            'help': 'Configuration file',
        },
        'tag-group': {
            'help': 'Which tag group to use for tagging',
        }
    }
    required_options = ['config', 'tag-group']

    def sanity(self):
        check_for_commands(REQUIRED_CMDS)

    def execute(self):
        task = self.shared('brew_task')
        if not task:
            raise CIError('no brew task found')

        if task.scratch:
            self.info('cowardly refusing to tag scratch build')
            return

        allres = self.shared('results')
        if not allres:
            self.warn('no results found, skipping')
            return

        # accept only TEST_TYPE test types and passed results
        if [r for r in allres if r.test_type in TEST_TYPES and r.overall_result != 'PASS']:
            self.warn('some tests failed, cannot apply the tag')
            return

        # read yaml configuration
        self.config = load_yaml(self.option('config'), logger=self.logger)

        # get tag group configuration
        group = self.option('tag-group')
        try:
            tag_map = self.config[group][0]
        except KeyError:
            raise CIError("unknown tag group '{}'".format(group))

        try:
            tag = tag_map[task.target.target]
        except KeyError:
            self.info("no tags to apply for build target '{}'".format(task.target))
            return

        self.info("applying tag '{}' for package '{}'".format(tag, task.nvr))
        command = ['brew', 'tag-build', tag, task.nvr]
        try:
            run_command(command)
        except CICommandError as exc:
            if 'already tagged' in exc.output.stdout:
                self.info('build already tagged, cowardly skipping')
            else:
                raise CIError("Failure during 'brew' execution: {}".format(exc.output.stdout + exc.output.stderr))
