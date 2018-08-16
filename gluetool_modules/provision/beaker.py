import collections
import re

import gluetool
from gluetool import GlueError
from gluetool.log import log_dict
from gluetool.utils import cached_property, normalize_multistring_option, normalize_path, load_yaml, dict_update
from libci.guest import NetworkedGuest


DEFAULT_SSH_OPTIONS = ['UserKnownHostsFile=/dev/null', 'StrictHostKeyChecking=no']

DEFAULT_ACTIVATION_TIMEOUT = 240
ACTIVATION_TICK = 5
DEFAULT_ECHO_TIMEOUT = 240
ECHO_TICK = 10
DEFAULT_BOOT_TIMEOUT = 240
BOOT_TICK = 10


StaticGuestDefinition = collections.namedtuple('StaticGuestDefinition', ('fqdn', 'arch'))

#: Beaker provisioner capabilities.
#: Follows :doc:`Provisioner Capabilities Protocol </protocols/provisioner-capabilities>`.
ProvisionerCapabilities = collections.namedtuple('ProvisionerCapabilities', ['available_arches'])


class BeakerGuest(NetworkedGuest):
    """
    Implements Beaker guest.
    """

    def _is_allowed_degraded(self, service):
        self.debug("service '{}' is degraded, check whether it's allowed".format(service))

        context = dict_update(self._module.shared('eval_context'), {
            'SERVICE': service
        })

        # placeholder for callbacks to propagate their decisions
        result = {
            'decision': False
        }

        # callback for `pattern` command
        def _pattern(instruction, command, argument, context):
            # pylint: disable=unused-argument

            # either a single pattern or a list of patterns
            patterns = [argument] if isinstance(argument, str) else argument

            self.debug('current decision: {}'.format(result['decision']))
            log_dict(self.debug, "matching service '{}' with patterns".format(service), patterns)

            if any(re.match(pattern, service) for pattern in patterns):
                self.debug("matched, service '{}' is allowed".format(service))

                result['decision'] = True

            return True

        # callback for `allow-any` command
        def _allow_any(instruction, command, argument, context):
            # pylint: disable=unused-argument

            self.debug('current decision: {}'.format(result['decision']))
            self.debug("matchig service '{}' with allow-any '{}'".format(service, argument))

            if argument.lower() in ('yes', 'true'):
                self.debug("matched, service '{}' is allowed".format(service))

                result['decision'] = True

            return True

        # First allowed by rules decides - therefore all callbacks return True to signal
        # they act on the given instruction.
        self._module.shared('evaluate_instructions', self._module.degraded_services_map, {
            'pattern': _pattern,
            'allow-any': _allow_any
        }, context=context, stop_at_first_hit=True)

        self.debug("final decision for service '{}' is {}".format(service, result['decision']))
        return result['decision']

    def _wait_alive(self):
        """
        Wait till the guest is alive. That covers several checks.
        """

        try:
            return self.wait_alive(connect_timeout=self._module.option('activation-timeout'), connect_tick=1,
                                   echo_timeout=self._module.option('echo-timeout'), echo_tick=ECHO_TICK,
                                   boot_timeout=self._module.option('boot-timeout'), boot_tick=BOOT_TICK)

        except GlueError as exc:
            raise GlueError('Guest failed to become alive: {}'.format(exc.message))

    #
    # "Public" API
    #
    def supports_snapshots(self):
        return False

    def create_snapshot(self, start_again=True):
        raise NotImplementedError()


class BeakerProvisioner(gluetool.Module):
    """
    Provides provisioning service on top of Beaker pool.

    .. note::

       As of now, a dummy provisioning is implemented - specify a list of FQDN:ARCH pairs, and the module
       would pretend it has provisioned these guests, wrapping them with necessary classes.
    """

    name = 'beaker-provisioner'
    description = 'Provisions guests from Beaker pool.'

    options = [
        ('Guest options', {
            'ssh-key': {
                'help': 'Path to SSH public key file'
            },
            'ssh-user': {
                'help': 'SSH username'
            },
            'ssh-options': {
                'help': 'SSH options',
                'action': 'append',
                'default': []
            }
        }),
        ('Provisioning options', {
            'static-guest': {
                'help': 'Wrap given machine and present it as "provisioned" guest.',
                'metavar': '(FQDN|IP):ARCH',
                'action': 'append',
                'default': []
            }
        }),
        ('Workarounds', {
            'degraded-services-map': {
                'help': 'Mapping of services which are allowed to be degraded while checking boot process status.'
            },
            'activation-timeout': {
                # pylint: disable=line-too-long
                'help': 'Wait SECOND for a guest to become reachable over network (default: {})'.format(DEFAULT_ACTIVATION_TIMEOUT),
                'type': int,
                'default': DEFAULT_ACTIVATION_TIMEOUT,
                'metavar': 'SECONDS'
            },
            'echo-timeout': {
                'help': 'Wait SECOND for a guest shell to become available (default: {})'.format(DEFAULT_ECHO_TIMEOUT),
                'type': int,
                'default': DEFAULT_ECHO_TIMEOUT,
                'metavar': 'SECONDS'
            },
            'boot-timeout': {
                # pylint: disable=line-too-long
                'help': 'Wait SECONDS for a guest to finish its booting process (default: {})'.format(DEFAULT_BOOT_TIMEOUT),
                'type': int,
                'default': DEFAULT_BOOT_TIMEOUT,
                'metavar': 'SECONDS'
            }
        })
    ]

    shared_functions = ('provision', 'provisioner_capabilities')

    @cached_property
    def degraded_services_map(self):
        if not self.option('degraded-services-map'):
            return []

        return load_yaml(self.option('degraded-services-map'), logger=self.logger)

    @cached_property
    def static_guests(self):
        return [
            StaticGuestDefinition(fqdn, arch) for fqdn, arch in [
                definition.split(':') for definition in normalize_multistring_option(self.option('static-guest'))
            ]
        ]

    def provisioner_capabilities(self):
        """
        Return description of Beaker provisioner capabilities.

        Follows :doc:`Provisioner Capabilities Protocol </protocols/provisioner-capabilities>`.
        """

        return ProvisionerCapabilities(
            available_arches=[
                guest.arch for guest in self.static_guests
            ]
        )

    def provision(self, environment, **kwargs):
        # pylint: disable=unused-argument

        ssh_options = normalize_multistring_option(self.option('ssh-options'))

        static_guests = normalize_multistring_option(self.option('static-guest'))

        guests = []

        for guest_spec in static_guests:
            try:
                fqdn, arch = guest_spec.split(':')

            except ValueError:
                raise GlueError("Static guest format is FQDN:ARCH, '{}' is not correct".format(guest_spec))

            # `arch` is valid keyword arg, but pylint doesn't agree... no idea why
            # pylint: disable=unexpected-keyword-arg
            guest = BeakerGuest(self, fqdn,
                                name=fqdn,
                                username=self.option('ssh-user'),
                                key=normalize_path(self.option('ssh-key')),
                                options=ssh_options,
                                arch=arch)

            guests.append(guest)

        self.debug('created {} guests, waiting for them to become alive'.format(len(guests)))

        for guest in guests:
            # pylint: disable=protected-access
            # guest is already running, check its status before moving on
            guest._wait_alive()

        self.info('provisioned {} guests'.format(len(guests)))

        log_dict(self.debug, 'guests', guests)

        return guests
