import libci


class GuestSetup(libci.Module):
    """
    Prepare guests for testing process. This is implemented by Ansible
    playbooks. When asked, module will play them on provided guests.
    """

    name = 'guest-setup'

    options = {
        'playbooks': {
            'help': 'Comma-separated list of Ansible playbooks to execute on guests.'
        }
    }

    required_options = ('playbooks',)

    shared_functions = ('setup_guest',)

    def setup_guest(self, hosts, **kwargs):
        """
        Setup provided guests using predefined list of Ansible playbooks.

        :param list host: Hosts specification, forming Ansible inventory.
        :param dict kwargs: Additional arguments which will be passed to
          `run_playbook` shared function of :py:class:`libci.modules.helpers.ansible.Ansible`
          module.
        """

        self.require_shared('run_playbook')

        for playbook in [playbook.strip() for playbook in self.option('playbooks').split(',')]:
            self.info("setting the guests '{}' up with '{}'".format(', '.join(hosts), playbook))

            self.shared('run_playbook', playbook, hosts, **kwargs)
