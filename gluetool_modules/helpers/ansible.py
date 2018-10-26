import json

import gluetool
from gluetool.utils import Command
from libci.sentry import PrimaryTaskFingerprintsMixin


class PlaybookError(PrimaryTaskFingerprintsMixin, gluetool.GlueError):
    def __init__(self, task, ansible_output, fatal_reports, fatal_messages):
        super(PlaybookError, self).__init__(task, PlaybookError.exception_message(fatal_messages))

        self.ansible_output = ansible_output
        self.fatal_reports = fatal_reports
        self.fatal_messages = fatal_messages

    @staticmethod
    def log_ansible_fatals(module, output):
        fatal_reports = []

        # Simple iterating over lines in `output.stdout` isn't good enough
        # since we might need to move on to the next line. Therefore we prepare
        # our own generator providing lines.
        def output_lines():
            for line in output.stdout.split('\n'):
                yield line.strip()

        # The generator cannot be "anonymous", instantiated once in the loop control. It must have a name
        # because we want to call its `next()` on demand.
        iter_lines = output_lines()

        for line in iter_lines:
            if not line.startswith('fatal: '):
                continue

            # Try to decode the line as a JSON object first - this is the default output "structure".
            try:
                fatal_reports.append(json.loads(line[line.index('{'):]))

            except ValueError:
                # Failed to parse JSON? Maybe it's YAML - in that case, pop next line, it contains
                # the message. Probably. Best we can do at this moment, sadly, Ansible YAML is not YAML :/
                fatal_reports.append(gluetool.utils.from_yaml(next(iter_lines)))

        fatal_messages = [
            report['msg'] for report in fatal_reports if 'msg' in report
        ]

        gluetool.log.log_dict(module.debug, 'fatal Ansible reports', fatal_reports)

        return fatal_reports, fatal_messages

    @staticmethod
    def exception_message(fatal_messages):
        if fatal_messages:
            return 'Failure during Ansible playbook execution: {}'.format(fatal_messages[-1])

        return 'Failure during Ansible playbook execution'


class Ansible(gluetool.Module):
    """
    Helper module - give it a playbook, a guest, maybe few additional variables,
    and let Ansible perform it.

    Usually, guests are provided by other provisioning modules, e.g. ``openstack``
    or ``docker-provisioner``, playbooks are up to you.
    """

    name = 'ansible'
    description = 'Run an Ansible playbook on a given guest.'

    options = {
        'ansible-playbook-options': {
            'help': "Additional ansible-playbook options, for example '-vvv'. (default: none)",
            'action': 'append',
            'default': []
        }
    }

    shared_functions = ('run_playbook',)

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    @gluetool.utils.cached_property
    def additional_options(self):
        return gluetool.utils.normalize_multistring_option(self.option('ansible-playbook-options'))

    def run_playbook(self, playbook_path, guests, variables=None, inventory=None):
        """
        Run Ansible playbook.

        :param str playbook_path: Path to the playbook.
        :param list(libci.guest.NetworkedGuest) guests: Guests to run playbooks on.
        :param dict variables: If set, represents additional variables that will
          be passed to ``ansible-playbook`` using ``--extra-vars`` option.
        :param str inventory: A path to the inventory file. You can use it if you
          want to cheat the ansible module e.g. to overshadow localhost with another host.
        :returns: :py:class:`gluetool.utils.ProcessOutput` instance.
        """

        playbook_path = gluetool.utils.normalize_path(playbook_path)
        self.debug("running playbook '{}'".format(playbook_path))

        if not all([guest.key == guests[0].key for guest in guests]):
            raise gluetool.GlueError('SSH key must be the same for all guests')

        inventory = inventory or '{},'.format(','.join([guest.hostname for guest in guests]))  # note the comma

        cmd = [
            'ansible-playbook',
            '-i', inventory,
            '--private-key', guests[0].key
        ]

        if variables:
            self.debug('variables:\n{}'.format(gluetool.log.format_dict(variables)))

            cmd += [
                '--extra-vars',
                ' '.join(['{}="{}"'.format(k, v) for k, v in variables.iteritems()])
            ]

        cmd += self.additional_options

        if not self.dryrun_allows('Running a playbook in non-check mode'):
            self.debug("dry run enabled, telling ansible to use 'check' mode")

            cmd += ['-C']

        cmd += [playbook_path]

        try:
            return Command(cmd, logger=self.logger).run()

        except gluetool.GlueCommandError as e:
            fatal_reports, fatal_messages = PlaybookError.log_ansible_fatals(self, e.output)

            primary_task = self.shared('primary_task')
            if primary_task:
                raise PlaybookError(primary_task, e.output, fatal_reports, fatal_messages)

            raise gluetool.GlueError(PlaybookError.exception_message(fatal_messages))
