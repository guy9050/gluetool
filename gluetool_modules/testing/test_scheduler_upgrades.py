import gluetool
import re
import ftplib

from gluetool.log import log_dict
from six.moves.urllib.parse import urlsplit
from rpmUtils.miscutils import splitFilename


class TestSchedulerUpgrades(gluetool.Module):

    name = 'test-scheduler-upgrades'
    description = 'Prepare schedule for upgrade testing. Modify schedule entries provided by previous (STI) provider.'

    options = {
        'compose-server-address': {
            'help': 'address of server, where OSCI composes are stored'
        },
    }

    required_options = ('compose-server-address',)
    shared_functions = ['create_test_schedule']

    def nvr_to_nevr_pattern(self, nvr, component):
        nevr_pattern = nvr.replace('{}-'.format(component), r'{}-\d+:'.format(re.escape(component)))
        nevr_pattern = '{}.src'.format(nevr_pattern)
        self.debug('nevr regex: "{}"'.format(nevr_pattern))

        return nevr_pattern

    def nevr_to_nvr(self, nevr):
        nvr = re.sub(r'-\d+:', '-', nevr)
        self.debug('nvr: "{}"'.format(nvr))

        return nvr

    def create_test_schedule(self, testing_environment_constraints=None):
        """
        This module modifies STI test schedule provided by other module. It expects one of the test is testing upgrade
        and require special variables for successful run. Namely url of composes, made by OSCI guys based on tested
        artifact and list of binary package names, which belongs to the artifact.
        """
        self.require_shared('primary_task')
        primary_task = self.shared('primary_task')

        matched_target = re.match(r'(rhel-\d\.\d\.\d(?:-z)?)-.*', primary_task.target)

        if matched_target is None:
            raise gluetool.GlueError('Unsupported primary task target: {}'.format(primary_task.target))

        composes_subdir_path = 'comp/{}'.format(matched_target.group(1))

        # Url of compose is found by name.
        # Name has following structure: {task_id}-{compose_id}-{component_name}
        # Based on known `task_id` and `component_name` most recent compose (with highest `compose_id`) is found.

        compose_name_regex = re.compile(r'{}/{}-(\d+)-{}'.format(
            re.escape(composes_subdir_path),
            re.escape(str(primary_task.id)),
            re.escape(primary_task.component)
        ))

        compose_server_url = self.option('compose-server-address')

        ftp = ftplib.FTP(urlsplit(compose_server_url).netloc)
        ftp.login()
        composes = ftp.nlst(composes_subdir_path)

        # Apply pattern to all composes. This will yield some "no match" values (match returns None in such case), but
        # we'll get rid of them later.
        matches = [
            (compose, compose_name_regex.match(compose))
            for compose in composes
        ]

        # Find the biggest match group, but consider only actual matches, skipping those "no match" values mentioned
        # above.
        try:
            winning_pair = max(
                [(compose, match) for compose, match in matches if match],
                key=lambda pair: int(pair[1].group(1))
            )
        except ValueError:
            raise gluetool.SoftGlueError(
                "Unable to find OSCI compose for {} ({}) in '{}' directory".format(
                    primary_task.component,
                    primary_task.id,
                    composes_subdir_path
                )
            )

        compose = winning_pair[0]

        primary_task_compose_url = '{}/{}'.format(compose_server_url, compose)

        self.info('OSCI compose found: {}'.format(primary_task_compose_url))

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

        binary_rpms_list = [package.encode('utf-8') for package in binary_rpms_list if package.endswith('.x86_64')]
        log_dict(self.debug, 'binary rpm nevrs', binary_rpms_list)

        # We have to transform `nevr` back to `nvr` by ourself, because `splitFilename` is unable to split `nevr`.
        # With python3 we can use `Subject` from `dnf` package
        # see https://bugzilla.redhat.com/show_bug.cgi?id=1452801#c7
        binary_rpms_list = [self.nevr_to_nvr(package) for package in binary_rpms_list]
        log_dict(self.debug, 'binary rpm nvrs', binary_rpms_list)

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
