import gluetool
from gluetool.log import log_dict
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
        extra_vars:
          ansible_python_interpreter: /usr/bin/python

    Each set specifies a ``rule`` key which is evaluated by ``rules-engine`` module. If it evaluates to ``True``,
    the value of ``playbooks`` replaces the list of playbooks to play. The dictionary extra_vars adds
    additional extra variables which should be run with playbooks. All variables are processed by Jinja2 templating
    engine, so you can use evaluation context variables if needed.
    """

    name = 'guest-setup'
    description = 'Prepare guests for testing process.'

    options = {
        'extra-vars': {
            'help': """
                    Comma-separated list of KEY=VALUE variables passed to ``run-playbook``
                    shared function. This option overrides mapped gathered from the mapping file
                    specified via the ``--playbooks-map`` option and also the shared function
                    variables argument (default: none).
                    """,
            'action': 'append',
            'default': []
        },
        'playbooks': {
            'help': """
                    Comma-separated list of Ansible playbooks to execute on guests,
                    overrides mapped values from ``--playbooks-map`` option (default: none).
                    """,
            'action': 'append',
            'default': []
        },
        'playbooks-map': {
            'help': 'Path to a file with preconfigured ``--playbooks`` options (default: %(default)s).',
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

    def _get_details_from_map(self):
        """ Returns a touple with list of playbooks and extra vars from the processed mapping file """

        playbooks = []
        extra_vars = {}

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

            if 'extra_vars' in playbooks_set:
                extra_vars = {
                    key: render_context(value) for key, value in playbooks_set['extra_vars'].iteritems()
                }

                gluetool.log.log_dict(self.debug, 'using these extra vars', extra_vars)

        return (playbooks, extra_vars)

    def setup_guest(self, guests, variables=None, **kwargs):
        """
        Setup provided guests using predefined list of Ansible playbooks.

        Only networked guests, accessible over SSH, are supported.

        :param list(libci.guest.NetworkedGuest) host: Guests to setup.
        :param dict kwargs: Additional arguments which will be passed to
          `run_playbook` shared function of :py:class:`gluetool_modules.helpers.ansible.Ansible`
          module.
        """

        self.require_shared('detect_ansible_interpreter')

        variables = variables or {}

        (playbooks, variables_from_map) = self._get_details_from_map()

        # updated variables with variables from mapping file
        variables.update(variables_from_map)

        # Detect Python interpreter for Ansible - this depends on the guest, it cannot be based
        # just on the artifact properties (some artifacts may need to be tested on a mixture
        # of different composes with different Python interpreters), therefore detect - unless,
        # of course, told otherwise by the caller.
        #
        # Also if user is specifying it's own playbooks, always autodetect ansible_python_interpreter
        if 'ansible_python_interpreter' not in variables or self.option('playbooks'):
            guests_interpreters = [
                self.shared('detect_ansible_interpreter', guest)
                for guest in guests
            ]

            log_dict(self.debug, 'detected interpreters', guests_interpreters)

            # If guests don't share the same set of interpreters, just give up - picking one common
            # to all guests is pointless, the correct fix would be to attach this info to each guest,
            # and let Ansible to consume it. On the other hand, Ansible module could detect it on
            # its own...
            if not all((interpreters == guests_interpreters[0] for interpreters in guests_interpreters)):
                self.warn('Python interpreters differ on guests, cannot pick one', sentry=True)

            # Corner case - detected list of interpreters is empty. On all guests, but still empty.
            elif not guests_interpreters[0]:
                # Using guest.warn to make the message guest-specific - after all, we were not
                # able to detect any intepreter on this guest, that's perfectly valid. We were
                # not able to detect them on *other* guests neither, but, well, who cares...
                guests[0].warn('Cannot deduce Python interpreter for Ansible', sentry=True)

            else:
                variables['ansible_python_interpreter'] = guests_interpreters[0][0]

        # ``--playbooks`` option overrides playbooks from mapping file
        if self.option('playbooks'):
            playbooks = gluetool.utils.normalize_path_option(self.option('playbooks'))

        # ``--extra_vars`` option overrides extra_vars from mapping file and shared function argument
        # convert the list to a dictionary which variables is expected to by run_playbook
        if self.option('extra-vars'):
            variables = gluetool.utils.normalize_multistring_option(self.option('extra-vars'))

            variables = {
                key: value for key, value in [var.split('=') for var in variables]
            }

        for playbook in playbooks:
            self.info("setting the guests '{}' up with '{}'".format(', '.join([guest.hostname for guest in guests]),
                                                                    playbook))

            self.shared('run_playbook', playbook, guests, variables=variables, **kwargs)

    def execute(self):
        self.require_shared('run_playbook')

        if self.option('playbooks-map'):
            self.require_shared('evaluate_rules')
