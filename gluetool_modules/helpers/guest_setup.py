import gluetool

from gluetool.utils import normalize_path_option, render_template


class GuestSetup(gluetool.Module):
    """
    Prepare guests for testing process. This is implemented by Ansible
    playbooks. When asked, module will play them on provided guests.

    The playbooks to play can be specified by following ways:

    * a configuration file, ``playbooks-map``, which specifies playbooks and conditions under
      which the playbook should be played on the guest.
    * the ``playbooks`` option can be used to force play from these playbooks, instead of playbooks
      provided by the configuration file


    playbooks-map
    =============

    .. code-block:: yaml

      ---
      # Default playbook to use on RHEL
      - rule: BUILD_TARGET.match('.*')
        playbooks:
          - ~/.citool.d/guest-setup/rhel/openstack-restraint.yaml

      # For RHEL8 packages use 1mt playbook for guest-setup
      - rule: BUILD_TARGET.match('rhel-8.0-candidate')
        playbooks:
          - ~/.citool.d/guest-setup/openstack-restraint-1mt.yaml

    Each set specifies a ``rule`` key which is evaluated by ``rules-engine`` module. If it evaluates to ``True``,
    the value of ``playbooks`` replaces the list of playbooks to play. The file is first processed
    by Jinja2 templating engine, so you can use evaluation context variables if needed.
    """

    name = 'guest-setup'
    description = 'Prepare guests for testing process.'

    options = {
        'playbooks': {
            'help': """Comma-separated list of Ansible playbooks to execute on guests,
                       overrides ``--playbooks-map`` option.""",
            'action': 'append',
            'default': []
        },
        'playbooks-map': {
            'help': 'Path to a file with preconfigured ``--playbooks`` options.',
            'default': None,
            'metavar': 'FILE'
        }
    }

    shared_functions = ('setup_guest',)

    def sanity(self):
        if not any([self.option('playbooks'), self.option('playbooks-map')]):
            raise gluetool.GlueError("One of the options 'playbooks' or 'playbooks-map' is required")

    @gluetool.utils.cached_property
    def playbooks_map(self):
        if not self.option('playbooks-map'):
            return []

        return gluetool.utils.load_yaml(self.option('playbooks-map'), logger=self.logger)

    def _get_playbooks_from_map(self):
        """ Returns a list of playbooks from the processed mapping file """

        playbooks = []

        def render_context(playbook):
            return render_template(playbook, logger=self.logger, **self.shared('eval_context'))

        for playbooks_set in self.playbooks_map:
            gluetool.log.log_dict(self.debug, 'evaluating following playbooks set rule', playbooks_set)

            if not self.shared('evaluate_rules',
                               playbooks_set.get('rule', 'False'),
                               context=self.shared('eval_context')):

                self.debug('rule does not match, moving on')
                continue

            if 'playbooks' in playbooks_set:
                playbooks = [render_context(pbook) for pbook in normalize_path_option(playbooks_set['playbooks'])]

                gluetool.log.log_blob(self.debug, 'using these playbooks', playbooks)

        return playbooks

    def setup_guest(self, guests, **kwargs):
        """
        Setup provided guests using predefined list of Ansible playbooks.

        Only networked guests, accessible over SSH, are supported.

        :param list(libci.guest.NetworkedGuest) host: Guests to setup.
        :param dict kwargs: Additional arguments which will be passed to
          `run_playbook` shared function of :py:class:`gluetool_modules.helpers.ansible.Ansible`
          module.
        """

        # ``--playbooks`` option overrides playbooks from mapping file
        if self.option('playbooks'):
            playbooks = gluetool.utils.normalize_path_option(self.option('playbooks'))
        else:
            playbooks = self._get_playbooks_from_map()

        for playbook in playbooks:
            self.info("setting the guests '{}' up with '{}'".format(', '.join([guest.hostname for guest in guests]),
                                                                    playbook))

            self.shared('run_playbook', playbook, guests, **kwargs)

    def execute(self):
        self.require_shared('run_playbook')

        if self.option('playbooks-map'):
            self.require_shared('evaluate_rules')
