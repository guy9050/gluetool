import gluetool


class GuestSetup(gluetool.Module):
    """
    Prepare guests for testing process. This is implemented by Ansible
    playbooks. When asked, module will play them on provided guests.
    """

    name = 'guest-setup'
    description = 'Prepare guests for testing process.'

    options = {
        'playbooks': {
            'help': 'Comma-separated list of Ansible playbooks to execute on guests.'
        }
    }

    required_options = ('playbooks',)

    shared_functions = ('setup_guest',)

    def setup_guest(self, guests, **kwargs):
        """
        Setup provided guests using predefined list of Ansible playbooks.

        Only networked guests, accessible over SSH, are supported.

        :param list(libci.guest.NetworkedGuest) host: Guests to setup.
        :param dict kwargs: Additional arguments which will be passed to
          `run_playbook` shared function of :py:class:`gluetool_modules.helpers.ansible.Ansible`
          module.
        """

        self.require_shared('run_playbook')

        for playbook in [playbook.strip() for playbook in self.option('playbooks').split(',')]:
            self.info("setting the guests '{}' up with '{}'".format(', '.join([guest.hostname for guest in guests]),
                                                                    playbook))

            self.shared('run_playbook', playbook, guests, **kwargs)
