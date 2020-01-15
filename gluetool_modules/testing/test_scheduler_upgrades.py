import gluetool
import re

from gluetool.log import log_dict
from gluetool_modules.libs.artifacts import splitFilename


class TestSchedulerUpgrades(gluetool.Module):

    name = 'test-scheduler-upgrades'
    description = 'Prepare schedule for upgrade testing. Modify schedule entries provided by previous (STI) provider.'

    shared_functions = ['create_test_schedule']

    def nvr_to_nevr_pattern(self, nvr, component):
        nevr_pattern = nvr.replace('{}-'.format(component), r'{}-\d+:'.format(re.escape(component)))
        nevr_pattern = '{}.src'.format(nevr_pattern)
        self.debug('nevr regex: "{}"'.format(nevr_pattern))

        return nevr_pattern

    def create_test_schedule(self, testing_environment_constraints=None):
        """
        This module modifies STI test schedule provided by other module. It expects one of the test is testing upgrade
        and require special variables for successful run. Namely url of composes, made by OSCI guys based on tested
        artifact and list of binary package names, which belongs to the artifact.
        """
        self.require_shared('primary_task')
        primary_task = self.shared('primary_task')

        primary_task_compose_url = self.shared('get_compose_url')

        # List of binary package names is obtained from componse metadata (metadata/rpms.json).

        metadate_rpms_json_path = '{}/metadata/rpms.json'.format(primary_task_compose_url)

        with gluetool.utils.requests(logger=self.logger) as requests:
            try:
                response = requests.get(metadate_rpms_json_path)
            except requests.exceptions.RequestException:
                raise gluetool.GlueError('Unable to fetch compose metadata from: {}'.format(metadate_rpms_json_path))

        metadate_rpms_json = response.json()

        primary_task_nevr_pattern = self.nvr_to_nevr_pattern(primary_task.nvr, primary_task.component)

        primary_task_nevr_regex = re.compile(primary_task_nevr_pattern)

        binary_rpms_list = []

        for repo_name in metadate_rpms_json['payload']['rpms']:
            for srpm_name in metadate_rpms_json['payload']['rpms'][repo_name]['x86_64']:
                if primary_task_nevr_regex.match(srpm_name):
                    binary_rpms_list = metadate_rpms_json['payload']['rpms'][repo_name]['x86_64'][srpm_name].keys()

        binary_rpms_list = [package.encode('utf-8') for package in binary_rpms_list if not package.endswith('.src')]
        log_dict(self.debug, 'binary rpm nevrs', binary_rpms_list)

        binary_rpms_list = [splitFilename(package)[0] for package in binary_rpms_list]
        log_dict(self.info, 'binary rpm names', binary_rpms_list)

        if not binary_rpms_list:
            self.warn('No binary rpm names found for package: "{}"'.format(primary_task_nevr_pattern))

        new_variables = {
            'compose_url': primary_task_compose_url,
            'binary_rpms_list': binary_rpms_list
        }

        schedule = self.overloaded_shared(
            'create_test_schedule',
            testing_environment_constraints=testing_environment_constraints
        )

        for schedule_entry in schedule:
            log_dict(self.debug, 'old variables', schedule_entry.variables)

            # `schedule_entry.variables` can contain variables given by user, we do not want to overwrite them
            new_variables.update(schedule_entry.variables)
            schedule_entry.variables = new_variables

            log_dict(self.debug, 'new variables', schedule_entry.variables)

        return schedule
