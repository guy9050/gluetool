"""
Guest cache
-----------

Consists of three types of cache keys:

List of cached guests
~~~~~~~~~~~~~~~~~~~~~

Named ``environments.<environment.compose>.<environment.arch>.guests``, stores a list of guests' names (FQDN). These
names represent guests known to cache, providing the given testing environment.

Guests's *in use* flag
~~~~~~~~~~~~~~~~~~~~~~

Named ``guests.<guest FQDN>.in-use``. Set to ``true`` when the guest is in cache but it's being used by some process
for testing. Guests with ``false`` in-use flag are available to grab.

Guest's *use-by* flag
~~~~~~~~~~~~~~~~~~~~~

Named ``guests.<guest FQDN>.use-by``. Set to a UTC timestamp after which the guest should not be used anymore.
Periodically being updated when extending guest's reservation timer. When the guest is not in use but its ``use-by``
flag has passed by, the guest should be removed from the cache since the actual machine is probably back in
the hands of Beaker.

Guests' info
~~~~~~~~~~~~

Named ``guests.<guest FQDN>.info``. Contains a mapping of values necessary to construct ``BeakerGuest`` instance
usable for running tests - FQDN, username, SSH key and options, architecture, etc.
"""

import collections
import datetime
import re
import threading

import gluetool
from gluetool import GlueError
from gluetool.log import log_dict
from gluetool.utils import cached_property, normalize_multistring_option, normalize_path, load_yaml, dict_update
from libci.guest import NetworkedGuest

import gluetool_modules.libs
from gluetool_modules.libs.testing_environment import TestingEnvironment


DEFAULT_SSH_OPTIONS = ['UserKnownHostsFile=/dev/null', 'StrictHostKeyChecking=no']

#: A port on which ``restraintd`` listens for connections to run tests. Since we're using ``restraint``
#: to run the pseudo-tests that provision the machine - and this ``restraintd`` instance keeps running
#: since there's still and unfinished test, ``reservesys``, we need an extra ``restraintd`` instance
#: to run the actual tests. And this is its port.
DEFAULT_RESTRAINTD_PORT = 8082

DEFAULT_ACTIVATION_TIMEOUT = 240
ACTIVATION_TICK = 5
DEFAULT_ECHO_TIMEOUT = 240
ECHO_TICK = 10
DEFAULT_BOOT_TIMEOUT = 240
BOOT_TICK = 10

DEFAULT_EXTENDTESTTIME_CHECK_TIMEOUT = 3600
DEFAULT_EXTENDTESTTIME_CHECK_TICK = 30

# 99 would be the most common value, however, we suspect Beaker checks for frequent extends of 99 hours,
# silently "stealing" such machines back for its pool. Hence the slightly lower value, hopefuly avoiding
# any suspicion from Beaker.
DEFAULT_RESERVATION_EXTENSION = 97
DEFAULT_REFRESHER_PERIOD = 4 * 3600  # 4 hours should be perfectly fine

StaticGuestDefinition = collections.namedtuple('StaticGuestDefinition', ('fqdn', 'arch'))

#: Beaker provisioner capabilities.
#: Follows :doc:`Provisioner Capabilities Protocol </protocols/provisioner-capabilities>`.
ProvisionerCapabilities = collections.namedtuple('ProvisionerCapabilities', ['available_arches'])


def _time_from_now(**kwargs):
    """
    Returns "deadline": "now", extended by ``timedelta`` which is constructed using given keyword arguments.
    """

    return datetime.datetime.utcnow() + datetime.timedelta(**kwargs)


class BeakerGuest(NetworkedGuest):
    """
    Implements Beaker guest.
    """

    def __init__(self, module, fqdn, is_static=False, **kwargs):
        super(BeakerGuest, self).__init__(module, fqdn, **kwargs)

        self._is_static = is_static

        # Lock guards access to the timer. If the timer is None, no use of the guest is allowed.
        # To cancel the refresh, grab the lock, cancel timer and set it to None - timer will either
        # quit because it didn't reach the period, or (corner case) it already started the refresh
        # function and in that case, it was waiting for the lock. When the lock becomes available,
        # refresh function checks timer for being None, and since it now is None, immediately quits.
        self._reservation_refresh_lock = threading.Lock()
        self._reservation_refresh_timer = None

    def _is_allowed_degraded(self, service):
        self._module.require_shared('evaluate_instructions')

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
            raise GlueError('Guest failed to become alive: {}'.format(exc))

    def _extend_reservation(self, hours=None):
        """
        Extend guest reservation by ``hours`` hours.

        :param int hours: number of hours (from *now*) to extend reservation by. If not set, value of
            ``reservation-extension`` is used.
        """

        hours = hours or self._module.option('reservation-extension')

        use_by = str(_time_from_now(hours=hours))

        self.execute('extendtesttime.sh <<< {}'.format(hours))

        self.info('extended reservation till cca {} UTC ({} hours from now)'.format(use_by, hours))

        if not self._is_static and self._module.use_cache:
            # pylint: disable=protected-access

            self._module._touch_dynamic_guest(self, use_by)

    def _refresh_reservation(self):
        """
        Extend guest reservation, and schedule next tick of reservation refresh.

        Heart of the refresh timer - gets called, extends reservation and schedules its own call.
        """

        # First, we must grab the lock.
        with self._reservation_refresh_lock:
            # If there's no timer, the refresh has been canceled but this method was already started
            # by timer thread. Quit immediately.
            if self._reservation_refresh_timer is None:
                return

            self._extend_reservation()

            # Schedule the next tick.
            self._reservation_refresh_timer = threading.Timer(self._module.option('reservation-extension-tick'),
                                                              self._refresh_reservation)

        next_tick = _time_from_now(seconds=DEFAULT_REFRESHER_PERIOD)
        self.debug('scheduled next reservation refresh tick to {}'.format(next_tick))

    def start_reservation_refresh(self):
        """
        Start reservation refresh process in the background.
        """

        # We call this method to start a refresh, when it comes to freshly reserved boxes,
        # it gets called when the dummy task, which serves as a jobwatch roadblock,
        # finished. It may happen that it gets called before reservesys starts - and extendtesttime.sh
        # is created by that task, so, our attempt to call the script would fail. To prevent that,
        # let's wait till the script appears.
        #
        # Machines from cache already passed this test, therefore in their case, this should
        # be just a quick sanity check.
        def _check_extendtesttime():
            try:
                self.execute('type extendtesttime.sh')

            except gluetool.utils.GlueCommandError:
                self.debug('extendtesttime.sh does not exist yet')

                return False

            return True

        gluetool.utils.wait('check whether extendtesttime.sh exists', _check_extendtesttime,
                            logger=self.logger,
                            timeout=self._module.option('extend-test-time-check-timeout'),
                            tick=self._module.option('extend-test-time-check-tick'))

        # we need to initialize timer variable to something true-ish, otherwise _refresh_reservation would quit :)
        with self._reservation_refresh_lock:
            self._reservation_refresh_timer = True

        self._refresh_reservation()

    def stop_reservation_refresh(self):
        """
        Stop reservation refresh process.
        """

        # Grab the lock - this makes sure the value of self._reservation_refresh_timer we and _refresh_reservation
        # would see is consistent.
        with self._reservation_refresh_lock:
            # There's no timer pending? Perfect, quit.
            if self._reservation_refresh_timer is None:
                return

            # Cancel the timer - this should prevent it from firing...
            self._reservation_refresh_timer.cancel()

            # ... but it may have already fired, and we grabbed the lock just before _refresh_reservation could
            # do so. For such case, set timer to None as well - _refresh_reservation checks it right after
            # it grabs the lock, and it will quit.
            self._reservation_refresh_timer = None

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
        ('Cache control', {
            'use-cache': {
                'help': 'If specified, cache provisioned guests (default: %(default)s).',
                'action': 'store_true',
                'default': False
            },
            'cache-prefix': {
                'help': 'Prefix for cache keys, to allow private cache trees (default: %(default)s).',
                'type': str,
                'action': 'store',
                'default': ''
            }
        }),
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
            },
            'reservation-extension': {
                'help': 'Extend guest reservation by ``HOURS`` hours (default: %(default)s).',
                'metavar': 'HOURS',
                'type': int,
                'default': DEFAULT_RESERVATION_EXTENSION
            },
            'reservation-extension-tick': {
                'help': 'Extend reservation every ``SECONDS`` seconds (default: %(default)s).',
                'metavar': 'SECONDS',
                'type': int,
                'default': DEFAULT_REFRESHER_PERIOD
            },
            'restraintd-port': {
                'help': 'Port on which ``restraintd`` for running tests should listen to (default: %(default)s).',
                'metavar': 'PORT',
                'type': int,
                'default': DEFAULT_RESTRAINTD_PORT
            }
        }),
        ('Provisioning options', {
            'static-guest': {
                'help': 'Wrap given machine and present it as "provisioned" guest (default: none).',
                'metavar': '(FQDN|IP):ARCH',
                'action': 'append',
                'default': []
            },
            'provision': {
                'help': """
                        Provision given number of guests. Use ``--environment`` to specify
                        what should the guests provide.
                        """,
                'metavar': 'COUNT',
                'type': int
            },
            'environment': {
                'help': 'Environment to provision, e.g. ``arch=x86_64,compose=rhel-7.6``.',
                'metavar': 'key1=value1,key2=value2,...'
            },
            'setup-provisioned': {
                'help': "Setup guests after provisioning them. See 'guest-setup' module.",
                'action': 'store_true'
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
            },
            'extend-test-time-check-timeout': {
                'help': 'Wait SECONDS for extendtesttime.sh script to appear (default: %(default)s).',
                'type': int,
                'default': DEFAULT_EXTENDTESTTIME_CHECK_TIMEOUT,
                'metavar': 'SECONDS'
            },
            'extend-test-time-check-tick': {
                'help': 'Wait SECONDS before checking for extendtesttime.sh presence again (default: %(default)s).',
                'type': int,
                'default': DEFAULT_EXTENDTESTTIME_CHECK_TICK,
                'metavar': 'SECONDS'
            }
        })
    ]

    shared_functions = ('provision', 'provisioner_capabilities')

    def __init__(self, *args, **kwargs):
        super(BeakerProvisioner, self).__init__(*args, **kwargs)

        self._dynamic_guests = []

    @cached_property
    def use_cache(self):
        return gluetool.utils.normalize_bool_option(self.option('use-cache'))

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
                    guest.environment.arch for guest in self.static_guests
                ]
            )

        return ProvisionerCapabilities(
            available_arches=gluetool_modules.libs.ANY
        )

    def _acquire_dynamic_guests_from_beaker(self, environment):
        """
        Provision guests by reserving them in Beaker pool..

        :param tuple environment: description of the environment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.
        :rtype: list(BeakerGuest)
        :returns: List of provisioned guests.
        """

        self.debug('acquire dynamic guests from Beaker for {}'.format(environment))

        self.require_shared('beaker_job_xml', 'beaker_jobwatch',
                            'submit_beaker_jobs', 'beaker_jobs_results', 'parse_beaker_matrix_url')

        ssh_user = self.option('ssh-user')
        ssh_key = normalize_path(self.option('ssh-key'))
        ssh_options = normalize_multistring_option(self.option('ssh-options'))

        # Override whatever value ``wow`` might think is a suitable distro for this request - we don't care!
        # We want *exactly* the compose specified in the environment, on the exact architecture specified
        # by that environment, and nothing else. ``wow`` is smart and can guess a lot of things, with
        # the help of other modules (shared ``distro`` function) but by this time, given the most common
        # usage pattern, someone else already used ``wow`` to prepare the whole set of jobs, giving its
        # smart ass a chance to show off, now we are not interested in that anymore - give us what we want!
        #
        # Also, tell ``reservesys`` to start *another* ``restraintd`` instance: when ``RSTRNT_PORT`` variable
        # is set, ``reservesys`` starts new instance of ``restraintd``, listening on this port, aside from
        # the "original" ``restraintd`` (which is running the ``reservesys`` task :). Should anyone wanted
        # to use this guest to run tests via ``restraint`` (which is quite common...) they would need
        # a ``restraintd`` which is not occupied by running our provisioning pseudo-tests, and that's the
        # new ``restraintd`` listening on ``RSTRNT_PORT``.
        jobs = self.shared('beaker_job_xml', body_options=[
            '--no-reserve',
            '--task=/distribution/utils/dummy',
            '--last-task=RESERVETIME=86400 RSTRNT_PORT={} /distribution/reservesys'.format(
                self.option('restraintd-port')
            )
        ], options=[
            '--arch', environment.arch
        ], distros=[
            environment.compose
        ], extra_context={
            'PHASE': 'guest-provisioning',
            'ENVIRONMENT': environment
        })

        if len(jobs) != 1:
            raise GlueError('For an environment a single guest is needed, multiple jobs were returned instead')

        for i, job in enumerate(jobs):
            gluetool.log.log_xml(self.debug, 'guest provisioning job #{}'.format(i), job)

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
                        environment=environment, is_static=False,
                        name=fqdn,
                        username=ssh_user,
                        key=ssh_key,
                        options=ssh_options)
            for fqdn in systems
        ]

        log_dict(self.debug, 'guests', guests)

        return guests

    # Helper construction the actul keys - it prepends cache-prefix (if there's any) and adds slashes.
    # _key('foo', 'bar') => 'cache/prefix/foo/bar'
    def _key(self, *args):
        if self.option('cache-prefix'):
            args = (self.option('cache-prefix'),) + args

        return '/'.join(args)

    def _acquire_dynamic_guests_from_cache(self, environment):
        """
        Provision guests by picking them from the cache.

        :param tuple environment: description of the environment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.
        :rtype: list(BeakerGuest)
        :returns: List of provisioned guests.
        """

        self.debug('acquire dynamic guests from cache for {}'.format(environment))

        self.require_shared('cache')

        cache = self.shared('cache')

        # Check whether there's an entry for our environment. It's supposed to be a list of guest names,
        # but it may be missing, we might be the first one to check.
        cached_guests = cache.get(self._key('environments', environment.compose, environment.arch, 'guests'),
                                  default=None)

        # If there's no such entry or the list is empty, leave - we have nothing to pick from cache.
        if not cached_guests:
            self.debug('no guests cached')

            return []

        # Check each guest in the list and find the first one not in use. Try to grab it by marking it
        # as being used, and if we succeed, build a guest on top of its info.
        for cached_fqdn in cached_guests:
            self.debug('checking guest {} for availability'.format(cached_fqdn))

            guest_inuse_key = self._key('guests', cached_fqdn, 'in-use')

            # find current value of `in-use` sub-key
            in_use, cas_tag = cache.gets(guest_inuse_key, default=None)

            # we can get the default answer, `None` - between us getting list of guests and checking their
            # state, someone may have removed a guest from the list, therefore `in-use` key would be missing.
            if in_use is None:
                self.debug('  in-use info is not available')
                continue

            # if the value is anything but `False`, the guest us being used
            if in_use is not False:
                self.debug('  guest is being used')
                continue

            # It's not in use, nice. Let's change the value to "used". May fail if somebody else took
            # it (or removed it) before we grab it - in that case we bail out and try another cached guest.
            result = cache.cas(guest_inuse_key, True, cas_tag)
            if not result:
                self.debug('  failed to grab')
                continue

            # Good, it's ours! But we have to check its `use-by` stamp, it might be rotten.
            self.debug('grabbing {} from cache'.format(cached_fqdn))

            use_by = cache.get(self._key('guests', cached_fqdn, 'use-by'))

            # This probably should not happen, I'm not 100% sure - we own the guest,
            # no client should ever remove the `use-by` without owning the guest...
            # Raise a warning and try another guest - leaving this one reserved so
            # nobody else would touch it, our human overlords investigate the case.
            if not result:
                self.warn('Guest {} free for use byt has no use-by flag'.format(cached_fqdn), sentry=True)
                continue

            use_by = datetime.datetime.strptime(use_by, '%Y-%m-%d %H:%M:%S.%f')

            # We need some extra time to finish the provisioning, close the paperwork and so on,
            # so let's pretend the first chance we get to extend guest reservation would happen
            # an hours from now. Would the guest still be fine by that time?
            not_actually_now = _time_from_now(hours=1)

            self.debug('use-by {}, "now" {}'.format(use_by, not_actually_now))

            if use_by <= not_actually_now:
                self.debug('  use-by stamp is too old')

                self._remove_dynamic_guest_from_cache(cached_fqdn, environment)
                continue

            # Download the remaining info and create the guest instance.
            guest_info = cache.get(self._key('guests', cached_fqdn, 'info'))

            return [
                BeakerGuest(self, guest_info['fqdn'].encode('ascii'),
                            environment=environment, is_static=False,
                            name=guest_info['fqdn'].encode('ascii'),
                            username=guest_info['ssh_user'].encode('ascii'),
                            key=guest_info['ssh_key'].encode('ascii'),
                            options=[s.encode('ascii') for s in guest_info['ssh_options']])
            ]

        # Empty cache, all guests in use, removed, whatever - we failed, return empty list and fall back
        # to the slow path.
        return []

    def _release_dynamic_guest_to_cache(self, guest):
        """
        The guest is no longer in use, put it into the cache for others to make use of it.

        :param BeakerGuest guest: guest to release.
        """

        self.require_shared('cache')

        cache = self.shared('cache')

        # Store info block first - even if it already exists, it's safe, the guest is still reserved by us,
        # nobody else can not even read its info.
        guest.debug('updating cache info')

        cache.set(self._key('guests', guest.name, 'info'), {
            'fqdn': guest.name,
            'ssh_user': guest.username,
            'ssh_key': guest.key,
            'ssh_options': guest.options,
            'arch': guest.environment.arch
        })

        # Set its in-use - if the guest is not yet in the cache, this key won't be checked because
        # the hostname is not in the list of cached guests; if it *is* in the cache, it's fine
        # as well since we don't use the guest anymore, and after this, anyone else is free to grab
        # it from the cache.
        guest.debug('marking as not in use anymore')

        cache.set(self._key('guests', guest.name, 'in-use'), False)

        # Insert guest name into the list of cached guests, if it's not there. If it is, just bail,
        # the guest was already cached and we set its in-use to False in the previous step, the guest
        # no longer interests us in such case.
        guests_key = self._key('environments', guest.environment.compose, guest.environment.arch, 'guests')

        guest.debug('adding to list of cached guests')

        while True:
            guests, cas_tag = cache.gets(guests_key, default=None, cas_default='0')

            # Are we adding the very first guest?
            if guests is None:
                guests = [guest.name]

                result = cache.cas(guests_key, guests, cas_tag)

                if result is None:
                    # No such list existed. We add it (atomically) - either we succeed, and the we simply quit
                    # the loop, or we fail and in that case someone else already initialized the list, and all
                    # we have to do is simply spin the loop again - this time, gets() will return the list and
                    # we will update it in the code below.
                    if cache.add(guests_key, guests):
                        guest.debug('  initialized new list of cached guests')
                        break

                    guest.debug('  someone else initialized the list, restart loop')

                    continue

                if result is False:
                    # failed to update
                    guest.debug('  failed to update existing list of cached guests')

                    continue

                # cas succeeded
                guest.debug('  guest is now listed as cached')
                break

            # It's there already? Fine, we're done.
            if guest.name in guests:
                guest.debug('  guest is already in the cache')

                break

            guests.append(guest.name)

            if cache.cas(guests_key, guests, cas_tag):
                guest.debug('  guest is now listed as cached')

                break

    def _remove_dynamic_guest_from_cache(self, name, environment):
        """
        For some reason, the guest entry should be removed from the cache.
        We **must** own the guest entry to avoid any race conditions.

        :param str name: guest FQDN.
        """

        self.info("Removing guest '{}' from the cache".format(name))

        self.require_shared('cache')

        cache = self.shared('cache')

        # We own the guest entry, therefore nobody should even try to reads its properties,
        # but when it comes to the list of cached guests, we must be more careful.

        # these are safe, we own the guest - nothing even reads these without grabing the guest first
        cache.delete(self._key('guests', name, 'info'))
        cache.delete(self._key('guests', name, 'use-by'))

        # We can remove `in-use` as well - should anyone try to read it (because they found the guest
        # in the list of cached guests), the would see it's either `True` or missing, in both cases moving
        # to guests with better prospects.
        cache.delete(self._key('guests', name, 'in-use'))

        # Remove the guest from list of cached guests. When we're done, there should be no trace
        # of the guest.

        self.debug('removing from list of cached guests')

        guests_key = self._key('environments', environment.compose, environment.arch, 'guests')

        while True:
            guests, cas_tag = cache.gets(guests_key, default=None, cas_default='0')

            # Is the list empty? At least our guest should be there... Cache may have been pruned
            # by an external event, nothing to do.
            if not guests:
                self.debug('list of cached guests is empty')
                break

            # Now this one is strange as well, but may happen - we grabbed the guest, then fall
            # asleep. In the meantime, something killed the cache, other processes began filling
            # it with new entries, and we're wake up again - the list exists, but obviously doesn't
            # contain our guest. Log & quit.
            if name not in guests:
                self.debug('list exists but does not contain the guest')
                break

            guests.remove(name)

            if cache.cas(guests_key, guests, cas_tag):
                self.debug('guest removed from the list')
                break

    def _touch_dynamic_guest(self, guest, use_by):
        """
        Update guest's ``use-by`` flag.

        :param str use_by: time when the guest should not be used anymore.
        """

        self.require_shared('cache')

        self.shared('cache').set(self._key('guests', guest.name, 'use-by'), use_by)

    def _acquire_dynamic_guests(self, environment):
        """
        Provision guests dynamically.

        :param tuple environment: description of the envronment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.
        :rtype: list(BeakerGuest)
        :returns: List of provisioned guests.
        """

        self.debug('acquire dynamic guests for {}'.format(environment))

        if self.use_cache:
            guests = self._acquire_dynamic_guests_from_cache(environment)
            self._dynamic_guests += guests

            log_dict(self.debug, 'guests acquired from cache', guests)

            if guests:
                return guests

        guests = self._acquire_dynamic_guests_from_beaker(environment)
        self._dynamic_guests += guests

        log_dict(self.debug, 'guests acquired from Beaker', guests)

        return guests

    def _release_dynamic_guest(self, guest):
        """
        Mark the guest as no longer in user. It does not mean the guest is necessarily returned
        to the Beaker pool - if the cache is enabled, the guest is stored there.

        :param BeakerGuest guest: guest to release.
        """

        if self.use_cache:
            self._release_dynamic_guest_to_cache(guest)
            return

        self._destroy_dynamic_guest(guest)

    def _destroy_dynamic_guest(self, guest):
        # pylint: disable=no-self-use
        """
        Return the guest back to Beaker pool.

        :param BeakerGuest guest: guest to destroy.
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

        self.debug('provisioning dynamic guests for {}'.format(environment))

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

        self.debug('provision static guests for {}'.format(environment))

        guests = []

        for guest_def in self.static_guests:
            self.debug('possible static guest: {}'.format(guest_def))

            if environment.arch != guest_def.arch:
                self.debug('  incompatible architecture')
                continue

            # `arch` is valid keyword arg, but pylint doesn't agree... no idea why
            # pylint: disable=unexpected-keyword-arg
            guest = BeakerGuest(self, guest_def.fqdn,
                                environment=environment, is_static=True,
                                name=guest_def.fqdn,
                                username=self.option('ssh-user'),
                                key=normalize_path(self.option('ssh-key')),
                                options=normalize_multistring_option(self.option('ssh-options')))

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
            guest.start_reservation_refresh()

        self.info('provisioned {} guests'.format(len(guests)))

        return guests

    def sanity(self):
        if self.option('provision'):
            if not self.option('environment'):
                raise GlueError('You must specify ``--environment`` when using direct provisioning')

            self._config['environment'] = TestingEnvironment.unserialize_from_string(self.option('environment'))

    def execute(self):
        if self.option('provision'):
            guests = self.provision(self.option('environment'),
                                    count=self.option('provision'))

            if self.option('setup-provisioned'):
                for guest in guests:
                    guest.setup()

    def destroy(self, failure=None):
        if not self._dynamic_guests:
            return

        for guest in self._dynamic_guests:
            guest.stop_reservation_refresh()
            guest.destroy()

        self.info('successfully removed all guests')
