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

    def __init__(self, module, fqdn, is_static=False, **kwargs):
        super(BeakerGuest, self).__init__(module, fqdn, **kwargs)

        self._is_static = is_static

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

    def _extend_reservation(self):
        self.execute('extendtesttime.sh <<< 99')

    #
    # "Public" API
    #
    def supports_snapshots(self):
        return False

    def create_snapshot(self, start_again=True):
        raise NotImplementedError()

    def destroy(self):
        if self._is_static:
            return

        # pylint: disable=protected-access
        self._module._release_dynamic_guest(self)


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
                'help': 'SSH options (default: none).',
                'action': 'append',
                'default': []
            }
        }),
        ('Provisioning options', {
            'static-guest': {
                'help': 'Wrap given machine and present it as "provisioned" guest (default: none).',
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
                'help': 'Wait SECOND for a guest to become reachable over network (default: %(default)s)',
                'type': int,
                'default': DEFAULT_ACTIVATION_TIMEOUT,
                'metavar': 'SECONDS'
            },
            'echo-timeout': {
                'help': 'Wait SECOND for a guest shell to become available (default: %(default)s)',
                'type': int,
                'default': DEFAULT_ECHO_TIMEOUT,
                'metavar': 'SECONDS'
            },
            'boot-timeout': {
                # pylint: disable=line-too-long
                'help': 'Wait SECONDS for a guest to finish its booting process (default: %(default)s)',
                'type': int,
                'default': DEFAULT_BOOT_TIMEOUT,
                'metavar': 'SECONDS'
            }
        })
    ]

    shared_functions = ('provision', 'provisioner_capabilities')

    def __init__(self, *args, **kwargs):
        super(BeakerProvisioner, self).__init__(*args, **kwargs)

        self._dynamic_guests = []

    @cached_property
    def degraded_services_map(self):
        if not self.option('degraded-services-map'):
            return []

        return load_yaml(self.option('degraded-services-map'), logger=self.logger)

    @cached_property
    def static_guests(self):
        guests = []

        for guest_spec in normalize_multistring_option(self.option('static-guest')):
            try:
                fqdn, arch = guest_spec.split(':')

            except ValueError:
                raise GlueError("Static guest format is FQDN:ARCH, '{}' is not correct".format(guest_spec))

            guests.append(StaticGuestDefinition(fqdn, arch))

        log_dict(self.debug, 'static guest pool', guests)

        return guests

    def provisioner_capabilities(self):
        """
        Return description of Beaker provisioner capabilities.

        Follows :doc:`Provisioner Capabilities Protocol </protocols/provisioner-capabilities>`.
        """

        if self.static_guests:
            return ProvisionerCapabilities(
                available_arches=[
                    guest.arch for guest in self.static_guests
                ]
            )

        return ProvisionerCapabilities(
            available_arches=[
                'x86_64', 'aarch64', 'ppc64', 'ppc64le', 's390x'
            ]
        )

    def _acquire_dynamic_guests_from_beaker(self, environment):
        """
        Provision guests by reserving them in Beaker pool..

        :param tuple environment: description of the envronment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.
        :rtype: list(BeakerGuest)
        :returns: List of provisioned guests.
        """

        self.require_shared('beaker_job_xml', 'beaker_jobwatch',
                            'submit_beaker_jobs', 'beaker_jobs_results', 'parse_beaker_matrix_url')

        ssh_user = self.option('ssh-user')
        ssh_key = normalize_path(self.option('ssh-key'))
        ssh_options = normalize_multistring_option(self.option('ssh-options'))

        jobs = self.shared('beaker_job_xml', body_options=[
            '--no-reserve',
            '--task=/distribution/utils/dummy',
            '--last-task=RESERVETIME=24h /distribution/reservesys'
        ], options=[
            '--arch', environment.arch
        ])

        for i, job in enumerate(jobs):
            gluetool.log.log_xml(self.debug, 'job {}#'.format(i), job)

        beaker_ids = self.shared('submit_beaker_jobs', jobs)

        _, matrix_url = self.shared('beaker_jobwatch', beaker_ids,
                                    end_task='/distribution/utils/dummy', inspect=False)

        self.debug('matrix url: {}'.format(matrix_url))

        matrix_url_info = self.shared('parse_beaker_matrix_url', matrix_url)
        self.debug('matrix URL info: {}'.format(matrix_url_info))

        results = self.shared('beaker_jobs_results', matrix_url_info.job_ids)
        self.debug('all results: {}'.format(results))

        systems = []
        for result in results.itervalues():
            for recipe_set in result.find_all('recipeSet', response='ack'):
                for recipe in recipe_set.find_all('recipe'):
                    if not recipe.get('system'):
                        continue

                    systems.append(recipe.get('system').encode('ascii'))

        log_dict(self.debug, 'systems', systems)

        guests = [
            BeakerGuest(self, fqdn,
                        is_static=False,
                        name=fqdn,
                        username=ssh_user,
                        key=ssh_key,
                        options=ssh_options,
                        arch=environment.arch)
            for fqdn in systems
        ]

        log_dict(self.debug, 'guests', guests)

        self._dynamic_guests += guests

        return guests

    def _acquire_dynamic_guests(self, environment):
        """
        Provision guests dynamically.

        :param tuple environment: description of the envronment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.
        :rtype: list(BeakerGuest)
        :returns: List of provisioned guests.
        """

        # One day in the future, an internal cache of provisioned guests will be held.
        # guests = self._acquire_dynamic_guest_from_cache(environment)

        # if guests:
        #    return guests

        return self._acquire_dynamic_guests_from_beaker(environment)

    def _release_dynamic_guest(self, guest):
        # pylint: disable=no-self-use
        """
        Mark the guest as no longer in user.

        In the future, the guest will be added to a cache, at this moment it is simply returned
        back to Beaker's pool.

        :param BeakerGuest guest: guest to release.
        """

        guest.execute('return2beaker.sh')

    def _provision_dynamic_guests(self, environment):
        """
        Provision guests dynamically by either finding them in a cache or by picking new ones from
        Beaker pool.

        :param tuple environment: description of the envronment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.
        :rtype: list(BeakerGuest)
        :returns: List of provisioned guests.
        """

        return self._acquire_dynamic_guests(environment)

    def _provision_static_guests(self, environment):
        """
        Provision guests stacially. The list of known "static" guests is used as a "pool" from which
        matching guests are picked.

        :param tuple environment: description of the envronment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.
        :rtype: list(BeakerGuest)
        :returns: List of provisioned guests.
        """

        guests = []

        for guest_def in self.static_guests:
            self.debug('possible static guest: {}'.format(guest_def))

            if environment.arch != guest_def.arch:
                self.debug('  incompatible architecture')
                continue

            # `arch` is valid keyword arg, but pylint doesn't agree... no idea why
            # pylint: disable=unexpected-keyword-arg
            guest = BeakerGuest(self, guest_def.fqdn,
                                is_static=True,
                                name=guest_def.fqdn,
                                username=self.option('ssh-user'),
                                key=normalize_path(self.option('ssh-key')),
                                options=normalize_multistring_option(self.option('ssh-options')),
                                arch=guest_def.arch)

            guests.append(guest)

        return guests

    def provision(self, environment, **kwargs):
        # pylint: disable=unused-argument
        """
        Provision (possibly multiple) guests backed by Beaker machines.

        :param tuple environment: description of the envronment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.
        """

        self.info('provisioning guest for environment {}'.format(environment))

        if self.option('static-guest'):
            guests = self._provision_static_guests(environment)

        else:
            guests = self._provision_dynamic_guests(environment)

        if not guests:
            raise GlueError('Cannot provision a guest for given environment')

        log_dict(self.debug, 'provisioned guests', guests)

        self.debug('waiting for guests to become alive')

        for guest in guests:
            # pylint: disable=protected-access
            # guest is already running, check its status before moving on
            guest._wait_alive()
            guest._extend_reservation()

        self.info('provisioned {} guests'.format(len(guests)))

        return guests

    def destroy(self, failure=None):
        if not self._dynamic_guests:
            return

        for guest in self._dynamic_guests:
            guest.destroy()

        self.info('successfully removed all guests')
