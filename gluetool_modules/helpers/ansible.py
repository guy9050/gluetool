import os
import re

import gluetool
from gluetool.utils import Command, from_json
from gluetool.log import log_dict
from libci.sentry import PrimaryTaskFingerprintsMixin


class PlaybookError(PrimaryTaskFingerprintsMixin, gluetool.GlueError):
    def __init__(self, task, ansible_output):
        super(PlaybookError, self).__init__(task, 'Failure during Ansible playbook execution')

        self.ansible_output = ansible_output


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

    # pylint: disable=too-many-arguments
    def run_playbook(self, playbook_path, guests, variables=None, inventory=None, cwd=None, json_output=True):
        """
        Run Ansible playbook.

        :param str playbook_path: Path to the playbook.
        :param list(libci.guest.NetworkedGuest) guests: Guests to run playbooks on.
        :param dict variables: If set, represents additional variables that will
          be passed to ``ansible-playbook`` using ``--extra-vars`` option.
        :param str inventory: A path to the inventory file. You can use it if you
          want to cheat the ansible module e.g. to overshadow localhost with another host.
        :param str cwd: A path to a directory where ansible will be executed from.
        :param bool json_output: ansible returns response as json if set.
        :returns: tuple of two items: a :py:class:`gluetool.utils.ProcessOutput` instance
            storing outcome of Ansible run, and a data structure representing the JSON output
            produced, or None if ``json_output`` was set to ``False``.
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

        env_variables = os.environ.copy()

        if json_output:
            env_variables.update({'ANSIBLE_STDOUT_CALLBACK': 'json'})

        try:
            ansible_call = Command(cmd, logger=self.logger).run(cwd=cwd, env=env_variables)

        except gluetool.GlueCommandError as exc:
            ansible_call = exc.output

        if json_output:
            # With `-v` option, ansible-playbook produces additional output, placed before the JSON
            # blob. Find the first '{' on a new line, that should be the start of the actual JSON data.
            match = re.search(r'^{', ansible_call.stdout, flags=re.M)
            if not match:
                raise gluetool.GlueError('Ansible did not produce JSON output')

            ansible_json_output = from_json(ansible_call.stdout[match.start():])

            log_dict(self.debug, 'Ansible json output', ansible_json_output)

        else:
            ansible_json_output = None

        if ansible_call.exit_code != 0:
            primary_task = self.shared('primary_task')
            if primary_task:
                raise PlaybookError(primary_task, ansible_call)

            raise gluetool.GlueError('Failure during Ansible playbook execution')

        return ansible_call, ansible_json_output
