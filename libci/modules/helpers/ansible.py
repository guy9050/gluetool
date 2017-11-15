import json
import os

import libci
from libci.log import format_dict


class Ansible(libci.Module):
    """
    Helper module - give it a playbook, a guest, maybe few additional variables,
    and let Ansible perform it.

    Usually, guests are provided by other provisioning modules, e.g. ``openstack``
    or ``docker-provisioner``, playbooks are up to you.
    """

    name = 'ansible'
    description = 'Run an Ansible playbook on a given guest.'

    shared_functions = ('run_playbook',)

    supported_dryrun_level = libci.ci.DryRunLevels.DRY

    def run_playbook(self, playbook_path, hosts, variables=None):
        """
        Run Ansible playbook.

        :param str playbook_path: Path to the playbook.
        :param list(str) hosts: Specifications of hosts, forming Ansible inventory.
        :param dict variables: If set, represents additional variables that will
          be passed to ``ansible-playbook`` using ``--extra-vars`` option.
        :returns: :py:class:`libci.utils.ProcessOutput` instance.
        """

        playbook_path = os.path.expanduser(playbook_path)
        self.debug("running playbook '{}'".format(playbook_path))

        cmd = [
            'ansible-playbook',
            '-i', '{},'.format(','.join(hosts))  # note the comma
        ]

        if variables:
            self.debug('variables:\n{}'.format(format_dict(variables)))

            cmd += [
                '--extra-vars',
                ' '.join(['{}="{}"'.format(k, v) for k, v in variables.iteritems()])
            ]

        if not self.dryrun_allows('Running a playbook in non-check mode'):
            self.debug("dry run enabled, telling ansible to use 'check' mode")

            cmd += ['-C']

        cmd += [playbook_path]

        try:
            return libci.utils.run_command(cmd)

        except libci.CICommandError as e:
            self.error('Failure during ansible playbook execution')

            for line in e.output.stdout.split('\n'):
                line = line.strip()

                if not line.startswith('fatal: '):
                    continue

                message = json.loads(line[line.index('{'):])
                if 'msg' in message:
                    self.error('Ansible says: {}'.format(message['msg']))

            raise libci.CIError('Failure during Ansible playbook execution. See log for details.')
