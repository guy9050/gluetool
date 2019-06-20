import os

import gluetool
from gluetool.log import log_dict
from gluetool.utils import normalize_path_option, render_template
from gluetool_modules.libs.guest_setup import guest_setup_log_dirpath, GuestSetupOutput

# Type annotations
# pylint: disable=unused-import,wrong-import-order,ungrouped-imports
from typing import cast, TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union  # noqa

if TYPE_CHECKING:
    import libci.guest  # noqa
    import gluetool_modules.helpers.ansible  # noqa


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

    shared_functions = ['setup_guest']

    def sanity(self):
        # type: () -> None

        if not any([self.option('playbooks'), self.option('playbooks-map')]):
            raise gluetool.GlueError("One of the options 'playbooks' or 'playbooks-map' is required")

    @gluetool.utils.cached_property
    def playbooks_map(self):
        # type: () -> List[Any]

        if not self.option('playbooks-map'):
            return []

        return cast(
            List[Any],
            gluetool.utils.load_yaml(self.option('playbooks-map'), logger=self.logger)
        )

    def _get_details_from_map(self):
        # type: () -> Tuple[List[str], Dict[str, str]]
        """
        Returns a tuple with list of playbooks and extra vars from the processed mapping file
        """

        playbooks = []  # type: List[str]
        extra_vars = {}  # type: Dict[str, Any]

        def render_context(playbook):
            # type: (str) -> str

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

                gluetool.log.log_dict(self.debug, 'using these playbooks', playbooks)

            if 'extra_vars' in playbooks_set:
                extra_vars = {
                    key: render_context(value) for key, value in playbooks_set['extra_vars'].iteritems()
                }

                gluetool.log.log_dict(self.debug, 'using these extra vars', extra_vars)

        return (playbooks, extra_vars)

    def setup_guest(self,
                    guest,  # type: libci.guest.NetworkedGuest
                    variables=None,  # type: Optional[Dict[str, str]]
                    log_dirpath=None,  # type: Optional[str]
                    **kwargs  # type: Any
                   ):  # noqa
        # type: (...) -> List[GuestSetupOutput]
        """
        Setup provided guest using predefined list of Ansible playbooks.

        Only networked guests, accessible over SSH, are supported.

        :param libci.guest.NetworkedGuest guest: Guest to setup.
        :param dict(str, str) variables: additional variables to pass to each playbook.
        :param str log_dirpath: if specified, try to store all setup logs inside the given directory.
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
            guest_interpreters = self.shared('detect_ansible_interpreter', guest)

            log_dict(guest.debug, 'detected interpreters', guest_interpreters)

            if not guest_interpreters:
                guest.warn('Cannot deduce Python interpreter for Ansible', sentry=True)

            else:
                variables['ansible_python_interpreter'] = guest_interpreters[0]

        # ``--playbooks`` option overrides playbooks from mapping file
        if self.option('playbooks'):
            playbooks = gluetool.utils.normalize_path_option(self.option('playbooks'))

        # ``--extra_vars`` option overrides extra_vars from mapping file and shared function argument
        # convert the list to a dictionary which variables is expected to by run_playbook
        if self.option('extra-vars'):
            variables_serialized = gluetool.utils.normalize_multistring_option(self.option('extra-vars'))

            variables = {
                key: value for key, value in [var.split('=') for var in variables_serialized]
            }

        log_dirpath = guest_setup_log_dirpath(guest, log_dirpath)
        log_filepath = os.path.join(log_dirpath, 'guest-setup-output.txt')

        guest.info('setting up with playbooks {}'.format(', '.join(playbooks)))

        ansible_output = self.shared(
            'run_playbook',
            playbooks,
            guest,
            variables=variables,
            log_filepath=log_filepath,
            **kwargs
        )

        return [
            GuestSetupOutput(
                label='guest setup',
                log_path=log_filepath,
                additional_data=ansible_output
            )
        ]

    def execute(self):
        # type: () -> None

        self.require_shared('run_playbook')

        if self.option('playbooks-map'):
            self.require_shared('evaluate_rules')
