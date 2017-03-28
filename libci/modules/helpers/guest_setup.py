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
        :param list host: Hosts specification, forming Ansible inventory.
        :param dict kwargs: Additional arguments which will be passed to
          :py:method:`libci.modules.helpers.ansible.run_playbook`.
        """

        if not self.has_shared('run_playbook'):
            raise libci.CIError("Module requires Ansible support, did you include 'ansible' module?")

        for playbook in [playbook.strip() for playbook in self.option('playbooks').split(',')]:
            self.info("setting the guests '{}' up with '{}'".format(', '.join(hosts), playbook))

            self.shared('run_playbook', playbook, hosts, **kwargs)

    def execute(self):
        pass
