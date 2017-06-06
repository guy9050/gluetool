import json

import libci


class Ansible(libci.Module):
    """
    Helper module - give it a playbook, a host, maybe few additional variables,
    and let Ansible perform it.
    """

    name = 'ansible'

    shared_functions = ('run_playbook',)

    def run_playbook(self, playbook_path, hosts, variables=None):
        """
        Run Ansible playbook.

        :param str playbook_path: Path to the playbook.
        :param [str, ...] hosts: Specifications of hosts, forming Ansible inventory.
        :param dict variables: If set, represents additional variables that will
          be passed to ``ansible-playbook`` using ``--extra-vars`` option.
        :returns: :py:class:`libci.utils.ProcessOutput` instance.
        """

        self.debug("running playbook '{}'".format(playbook_path))

        cmd = [
            'ansible-playbook',
            '-i', '{},'.format(','.join(hosts))  # note the comma
        ]

        if variables:
            self.debug('variables:\n{}'.format(libci.utils.format_dict(variables)))

            cmd += [
                '--extra-vars',
                ' '.join(['{}="{}"'.format(k, v) for k, v in variables.iteritems()])
            ]

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
