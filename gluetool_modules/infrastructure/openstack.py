import collections
import errno
import gzip
import os
import re
import threading
from time import gmtime, strftime
from datetime import datetime, timedelta

import dateutil.parser
import pytz
import six

from keystoneclient.auth.identity import v3 as keystone_identity
from keystoneauth1 import session as keystone_session

import novaclient.exceptions
from novaclient import client
from novaclient.exceptions import BadRequest, NotFound, Unauthorized, NoUniqueMatch

import gluetool
from gluetool import GlueError, GlueCommandError
from gluetool.action import Action
from gluetool.log import LoggerMixin, format_dict, log_dict
from gluetool.result import Result
from gluetool.utils import cached_property, normalize_path, load_yaml, dict_update
from libci.guest import NetworkedGuest

from gluetool_modules.libs.testing_environment import TestingEnvironment


DEFAULT_FLAVOR = 'm1.small'
DEFAULT_NAME_TEMPLATE = 'citool-{{ GUEST_INDEX }}'
DEFAULT_RESERVE_DIR = '~/openstack-reservations'
DEFAULT_REMOTE_RESERVE_FILE = '~/.openstack-reservation'
DEFAULT_RESERVE_TIME = 24

DEFAULT_ACQUIRE_TIMEOUT = 240
DEFAULT_ACTIVATION_TIMEOUT = 240
ACTIVATION_TICK = 5
DEFAULT_ECHO_TIMEOUT = 240
ECHO_TICK = 10
DEFAULT_BOOT_TIMEOUT = 240
BOOT_TICK = 10

DEFAULT_START_AFTER_SNAPSHOT_ATTEMPTS = 3
DEFAULT_RESTORE_SNAPSHOT_ATTEMPTS = 3
DEFAULT_SHUTDOWN_TIMEOUT = 60

DEFAULT_SSH_OPTIONS = ['UserKnownHostsFile=/dev/null', 'StrictHostKeyChecking=no']
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


#: OpenStack provisioner capabilities.
#: Follows :doc:`Provisioner Capabilities Protocol </protocols/provisioner-capabilities>`.
ProvisionerCapabilities = collections.namedtuple('ProvisionerCapabilities', ['available_arches'])


def _call_api(logger, method_label, method, *args, **kwargs):
    with Action('query OpenStack API', parent=Action.current_action(), logger=logger, tags={
        'method': method_label,
        'positional-arguments': args,
        'keyword-arguments': kwargs
    }):
        return method(*args, **kwargs)


class OpenStackImage(LoggerMixin, object):
    """
    Represents an OpenStack image, allowing consistent manipulation of images, snapshots
    and their names and IDs in module internals, with few helper methods on top of that.
    """

    def __init__(self, module, name, resource=None):
        super(OpenStackImage, self).__init__(module.logger)

        self.module = module
        self.name = name
        self._resource = resource

        glance_options = {}
        for option in ['auth-url', 'project-name', 'username', 'password']:
            if self.module.option('glance.' + option):
                glance_options[option] = self.module.option('glance.' + option)
            else:
                glance_options[option] = self.module.option(option)

        self._glance_command = [
            'glance',
            '--os-auth-url', glance_options['auth-url'],
            '--os-project-name', glance_options['project-name'],
            '--os-username', glance_options['username'],
            '--os-password', glance_options['password']
        ]

    def __repr__(self):
        return '<OpenStackImage(name="{}")>'.format(self.name)

    @classmethod
    def factory(cls, module, image, **kwargs):
        if isinstance(image, cls):
            return image

        if isinstance(image, str):
            return cls(module, image, **kwargs)

        raise GlueError("Cannot convert '{}' of type {} to an OpenStackImage instance".format(image, type(image)))

    def resource(self, on_nonactive_raise=True, pick_most_recent=False):
        """
        Return reference to an Openstack image.

        :param bool on_nonactive_raise: raise exception if no active image found (default)
        :param bool pick_most_recent: when the image name returns multiple OpenStack images,
            pick the one updated most recently. If not set, raise an exception reporting
            multiple images were found.
        :raises gluetool.glue.GlueError: if no image found or
                                         if multiple images with same name found or
                                         if no active image found and on_nonactive_raise is True
        """
        self.debug("get image reference for '{}'".format(self.name))

        if self._resource:
            return self._resource

        try:
            image = self.module._call_api('images.find', self.module.nova.images.find, name=self.name)
            self._resource = image

        except NotFound:
            self.module._resource_not_found('images', self.name)

        except NoUniqueMatch:
            self.debug("found multiple images for name '{}'".format(self.name))

            if not pick_most_recent:
                raise GlueError("Image name '{}' references multiple images".format(self.name))

            images = [
                (image, image.to_dict())
                for image in self.module._call_api('images.findall', self.module.nova.images.findall, name=self.name)
            ]

            log_dict(self.debug, 'found images', images)

            for _, image_info in images:
                image_info['updated-comparable'] = dateutil.parser.parse(image_info['updated']).astimezone(pytz.utc)

            log_dict(self.debug, 'found images with comparable timestamps', images)

            image, _ = sorted(images, key=lambda x: x[1]['updated-comparable'])[-1]

            self._resource = image

        self.debug("image settled on '{}' {} ({})".format(self._resource, self._resource.id, self._resource.status))

        if image.status != u'ACTIVE':
            # blow up if image is not ACTIVE and the caller wants this
            if on_nonactive_raise:
                raise GlueError("Image '{}' found but is not active".format(image.name))

            self.warn("Image '{}' found but is not active".format(self.name))

        return self._resource

    def release(self):
        self.resource(on_nonactive_raise=False).delete()

        self.debug('released the image')

    def download(self, filename=None):
        # This could be implemented using glanceclient API - calling `glance` is easy and quick
        # but leaks usernames and passwords into the log file. On the other hand, credentials are
        # in the log aready because gluetool logs every option of the module...

        filename = filename or '{}.img'.format(self.name)

        self.debug("downloading image '{}' into '{}'".format(self.name, filename))

        try:
            gluetool.utils.Command(self._glance_command + ['image-download',
                                                           '--file', filename,
                                                           str(self.resource().id)]).run()

        except gluetool.GlueCommandError as exc:
            raise GlueError('Failed to download snapshot: {}'.format(exc.output.stderr))

        return filename

    @cached_property
    def _image_info(self):
        try:
            image_info_cmd = gluetool.utils.Command(self._glance_command + ['image-show', str(self.resource().id)])
            output = image_info_cmd.run()

        except gluetool.GlueCommandError as exc:
            raise GlueError('Failed to get image info: {}'.format(exc.output.stderr))

        return output.stdout

    @cached_property
    def compose(self):
        match = re.findall('\"meta_compose=(.*)\"', self._image_info)

        if not match:
            self.warn('No compose found for image: {}'.format(self.name), sentry=True)
            return ''

        return match[0]


class OpenstackGuest(NetworkedGuest):
    """
    Implements Openstack Network Guest with snapshots support.
    """

    def _call_api(self, method_label, method, *args, **kwargs):
        return _call_api(self.logger, method_label, method, *args, **kwargs)

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

    #
    # Low-level API, dealing directly with OpenStack resources and objects.
    #

    @staticmethod
    def _acquire_os_resource(resource, logger, timeout, tick, func_label, func, *args, **kwargs):
        """
        Acquire a resource from OpenStack. If there are quotas in play, this method will handle
        "quota exceeded" responses, and will wait till the resource becomes available.

        ``func`` is one of ``novaclient`` methods, e.g. ``floating_ips.create``. When it is not
        possible to acquire a resource because of quota, these functions raise
        :py:class:`novaclient.exceptions.Forbidden` with known message.

        Note the ``logger`` attribute - this method is called usually when the guest object
        is not yet initialized (before calling super's ``__init__``), therefore there's no
        such thing as ``self.logger`` or ``self._module.logger``.

        :param str resource: Resource type (``instance``, ``floating IP``, etc.).
        :param gluetool.log.ContextLogger logger: Logger used for logging.
        :param int timeout: Timeout in seconds for acquiring the resource.
        :param int tick: Tick in seconds before retrying.
        :param str func_label: Getter label used for logging.
        :param callable func: Getter which, when called, will acquire the resource.
        :param tuple args: Positional arguments for ``func``.
        :param dict kwargs: Keyword arguments for ``func``.
        """

        def _ask():
            try:
                ret = _call_api(logger, func_label, func, *args, **kwargs)

                return Result.Ok(ret)

            except novaclient.exceptions.Forbidden as exc:
                if not exc.message.startswith('Quota exceeded'):
                    raise GlueError('Failed to acquire {}: {}'.format(resource, exc.message))

                # Original message "Quota exceeded for cores: Requested 8, but already used 77 of 80 cores" is good
                # enough for public use, we just add a bit of sugar to let user know we're working on it.
                logger.info('{}. Will try again in a moment.'.format(exc.message))

                # let wait() know we need to try again
                return Result.Error('failed to acquire resource: {}'.format(exc.message))

            except novaclient.exceptions.BadRequest as exc:
                # Handle floating IP not yet available for assignment
                if not exc.message.startswith('Instance network is not ready yet'):
                    raise GlueError('Failed to acquire {}: {}'.format(resource, exc.message))

                logger.info('{}. Will try again in a moment.'.format(exc.message))

                # let wait() know we need to try again
                return Result.Error('failed to acquire resource: {}'.format(exc.message))

        return gluetool.utils.wait('acquire {} from OpenStack'.format(resource),
                                   _ask,
                                   logger=logger,
                                   timeout=timeout,
                                   tick=tick)

    def _acquire_floating_ip(self):
        """
        Acquire floating IP
        """
        if not self._os_details['ip_pool_name']:
            return

        self._os_floating_ip = OpenstackGuest._acquire_os_resource(
            'floating IP',
            self._module.logger,
            self._module.option('acquire-timeout'),
            30,
            'floating_ips.create',
            self._nova.floating_ips.create,
            self._os_details['ip_pool_name']
        )

    def _acquire_nics(self):
        """
        Acquire list of networks.
        """

        if not self._os_details.get('network', None):
            return

        self._os_nics = [
            {
                'net-id': network.id
            } for network in self._os_details['network']
        ]

    def _acquire_instance(self, image=None):
        """
        Acquire an instance.
        """

        image = image or self._os_details['image']

        self._os_instance = OpenstackGuest._acquire_os_resource(
            'instance',
            self._module.logger,
            self._module.option('acquire-timeout'),
            30,
            'servers.create',
            self._nova.servers.create,
            name=self._os_name,
            flavor=self._os_details['flavor'],
            image=image.resource(pick_most_recent=True),
            nics=self._os_nics,
            key_name=self._os_details['key_name'],
            userdata=self._os_details['user_data']
        )

    def _acquire_network_ip(self):
        """
        Acquired IP address from the network. Must be called after instance is ACTIVE. Acquired
        only if a specific network was specified.
        """
        networks = self._os_details.get('network', None)

        if not networks:
            self._module.debug('skipped acquiring of network IP because no network was specified')
            return

        log_dict(self._module.debug, 'available networks', self._os_instance.networks)

        def _find_ip():

            # try to find a network IP, first wins
            for network in networks:
                try:
                    self._os_network_ip = self._os_instance.networks[network.label][0]
                    break

                except (KeyError, IndexError, TypeError):
                    pass

            return self._os_network_ip

        OpenstackGuest._acquire_os_resource(
            'Network IP',
            self._module.logger,
            self._module.option('acquire-timeout'),
            30,
            'instance.networks[0]',
            _find_ip
        )

    def _release_snapshots(self):
        """
        Removes all created snapshots.
        """

        for image in self._snapshots:
            image.release()

        if self._snapshots:
            self.debug('released all {} snapshots'.format(len(self._snapshots)))

        self._snapshots = []

    def _release_floating_ip(self):
        if not self._os_floating_ip:
            self.debug("skipped release of floating IP, because it is not available")
            return

        try:
            self._os_floating_ip.delete()

            self.debug("released floating IP '{}'".format(self.floating_ip))

        except NotFound:
            self.warn('associated floating IP already removed', sentry=True)

        self._os_floating_ip = None

    def _release_instance(self):
        """
        Release instance back to the pool.
        """

        # save console log, if possible
        try:
            filename = 'console-{}-{}.log.gz'.format(self.name, self.instance_id)

            self.debug("storing console output in '{}'".format(filename))

            console = self._call_api('instance.get_console_output', self._os_instance.get_console_output)

            if console:
                console = console.encode('utf-8', 'replace')

            else:
                # Some servers may return empty console output. Observed with rhel-7.1-server-x86_64-released image
                self.warn('empty console output')

                console = '<Server returned empty console output>'

            with gzip.open(filename, 'wb') as f:
                f.write(console)
                f.flush()

        except Exception as exc:
            self.warn('Failed to store console output in the file: {}'.format(str(exc)), sentry=True)

        try:
            self.debug('deleting...')

            self._call_api('instance.delete', self._os_instance.delete)

            self.debug('deleted')

        except NotFound:
            self.warn('Instance already deleted', sentry=True)

        finally:
            self._os_instance = None

    def _shutdown(self):
        """
        Shut down the instance.
        """

        self.debug('shutting down...')

        self._call_api('instance.stop', self._os_instance.stop)

        self._wait_shutoff()

        self.debug('shut down finished')

    def _start(self):
        """
        Start the instance.
        """

        self.debug('starting...')

        self._call_api('instance.start', self._os_instance.start)

        self._wait_active()

        self.debug('started')

    def _rebuild(self, image):
        """
        Rebuild the instance from an image.
        """

        self.debug('rebuilding...')

        original_status = self._get_resource_status('servers', self._os_instance.id)

        self._os_instance.rebuild(image.resource())

        if original_status == u'ACTIVE':
            self._wait_active()

        else:
            self._wait_shutoff()

        self.debug('rebuilt')

    def _reboot(self, reboot_type='SOFT'):
        """
        Reboot the instance.

        :param str reboot_type: Either ``SOFT`` - software level - or ``HARD`` - virtual power cycle.
        """

        self.debug('rebooting...')

        self._call_api('instance.reboot', self._os_instance.reboot, reboot_type)

        self._wait_active()

        self.debug('rebooted')

    def _wait_active(self):
        """
        Wait till OpenStack reports the instance is ``ACTIVE``.
        """

        self._wait_for_resource_status('instance reports ACTIVE', 'servers', self._os_instance.id, u'ACTIVE',
                                       timeout=self._module.option('activation-timeout'), tick=1)

    def _wait_shutoff(self):
        """
        Wait till OpenStack reports the instance is ``SHUTOFF``.
        """

        self._wait_for_resource_status('instance reports SHUTOFF', 'servers', self._os_instance.id, u'SHUTOFF',
                                       timeout=self._module.option('shutdown-timeout'), tick=1)

    def _get_resource_status(self, resource, rid):
        status = self._call_api('{}.find'.format(resource), getattr(self._nova, resource).find, id=rid).status

        self.debug("status of resource '{}' within '{}' is '{}'".format(rid, resource, status))

        return status

    def _check_resource_status(self, resource, rid, status):
        """
        Check whether the resource with given ID is in expected state.

        param: str resource: Resource type (``images``, ``servers``, etc.)
        param: unicode rid: ID of the resource to check.
        param: unicode status: Expected status of the resource. Note the ``unicode`` type.
        """

        return self._get_resource_status(resource, rid) == status

    def _wait_for_resource_status(self, label, resource, rid, status, timeout, tick):
        def _check():
            if self._check_resource_status(resource, rid, status):
                return Result.Ok(True)

            return Result.Error('resource {} {} not in state {}'.format(resource, rid, status))

        self.wait(label, _check, timeout=timeout, tick=tick)

    def _assign_floating_ip(self):
        """
        Assign floating IP.
        """

        # bail out if there is no floating IP to be assigned
        if not self.floating_ip:
            return

        # The assignment of IP can fail if done too early. So retry if needed to be sure
        # that we do not hit this. Also retrying should improve a bit situation with shorter
        # outages happening regularly on Openstack.

        def _assign():
            """
            The add_floating_ip returns an instance of novaclient.base.TupleWithMeta
            https://docs.openstack.org/python-novaclient/latest/reference/api/novaclient.v2.servers.html

            :param nova.novaclient.v2.floating_ips.FloatingIP floating_ip: floating IP to assign
            :returns: True if floating IP successfully assigned, False otherwise
            """

            return isinstance(self._os_instance.add_floating_ip(self.floating_ip), novaclient.base.TupleWithMeta)

        OpenstackGuest._acquire_os_resource(
            'IP assignment',
            self._module.logger,
            self._module.option('acquire-timeout'),
            1,
            'instance.add_floating_ip',
            _assign
        )

    def __init__(self, module, details=None, instance_id=None, **kwargs):
        self._snapshots = []

        self._nova = module.nova

        # this is done by parent's constructor but we need it sooner for our _acquire_* methods
        self._module = module

        # these are very close to underlying OpenStack resources
        self._os_name = None
        self._os_instance = None
        self._os_floating_ip = None
        self._os_network_ip = None
        self._os_nics = []
        self._os_details = details or {}

        # Initialize logging as early as possible, before we call anything else. Note that this would
        # yield "[<unknown guest>]" context in log messages, which wouldn't be much helpful. Try to
        # provide better name, but don't try that hard - if we're initializing from an existing instance,
        # we simply *do not* have any name at all.
        self.init_logging(
            module.logger,
            name=details.get('name', None) if details else None
        )

        # provision a new instance
        if instance_id is None:
            self._os_name = details['name']

            # re-initialize logging as we know the name by now
            self.init_logging(module.logger, name=self._os_name)

            self._acquire_floating_ip()
            self._acquire_nics()
            self._acquire_instance()

            # we need to wait for an instance to become active before getting IP from network
            if self._os_details.get('network', None):
                self._wait_active()
                self._acquire_network_ip()

            self._assign_floating_ip()

        # initialize from an existing instance
        else:
            self._os_details.update({
                'username': module.option('ssh-user'),
                'key': module.option('ssh-key')
            })

            self._os_instance = self._call_api('servers.find', self._nova.servers.find, id=instance_id)

            self._os_name = self._os_instance.to_dict()['name']

            # re-initialize logging as we know the name by now
            self.init_logging(module.logger, name=self._os_name)

            # we need to wait for an instance to become active before getting IP from network
            if self._os_details.get('network', None):
                self._wait_active()
                self._acquire_network_ip()

            # find floating IP only if ip pool name specified
            if self._os_details['ip_pool_name']:
                self._os_floating_ip = self._call_api(
                    'floating_ips.find',
                    self._nova.floating_ips.find,
                    instance_id=instance_id
                )

            self._os_name = self._os_instance.to_dict()['name']
            self._os_nics = self._acquire_nics()

            self._acquire_network_ip()

        super(OpenstackGuest, self).__init__(module,
                                             self.ip,
                                             name=self._os_name,
                                             username=self._os_details['username'],
                                             key=self._os_details['key'],
                                             options=DEFAULT_SSH_OPTIONS,
                                             **kwargs)

    @property
    def image(self):
        assert self._os_instance is not None

        img_id = self._os_instance.image['id']

        try:
            resource = self._call_api('images.findall', self._nova.images.findall, id=img_id)[0]

        except IndexError:
            raise GlueError("Cannot find image by its ID '{}'".format(img_id))

        return OpenStackImage(self._module, resource.name, resource=resource)

    @property
    def floating_ip(self):
        """
        Property provides associated floating IP address as a string.

        :returns: floating IP address of the guest or None if not available
        """
        return str(self._os_floating_ip.ip) if self._os_floating_ip else None

    @property
    def network_ip(self):
        """
        Property returns IP address from the instance's network as a string.

        :returns: IP address or None if not available
        """
        return str(self._os_network_ip) if self._os_network_ip else None

    @property
    def ip(self):
        """
        Property returns an available IP address of the machine, floating IP is preferred over network IP.

        :returns: An IP address available, floating IP is preferred over network IP.
        :raises gluetool.glue.GlueError: If no IP address available.
        """

        if not any([self.floating_ip, self.network_ip]):
            raise GlueError('No floating or network IP found, cannot continue')

        return self.floating_ip or self.network_ip

    @property
    def instance_id(self):
        """
        Provides instance ID as a string.

        :returns: string representation of instance ID
        """
        return str(self._os_instance.id)

    def _wait_alive(self):
        """
        Wait till the instance is alive. That covers several checks, and expects the instance to be ``ACTIVE``.
        """

        try:
            # First check the status of the instance - until it's ACTIVE, don't bother
            # to check anything else - our network-based checks *may* succeed even during
            # an instance shutdown process, leading to false positives.
            self._wait_active()

            # If the instance is in ACTIVE state, proceed with other checks
            return self.wait_alive(connect_timeout=self._module.option('activation-timeout'), connect_tick=1,
                                   echo_timeout=self._module.option('echo-timeout'), echo_tick=ECHO_TICK,
                                   boot_timeout=self._module.option('boot-timeout'), boot_tick=BOOT_TICK)

        except GlueError as exc:
            raise GlueError('Guest failed to become alive: {}'.format(exc.message))

    def _bring_alive(self, label, actor, attempts=1):
        """
        Try to perform an action, and then wait for instance to become alive.

        :param str label: For logging purposes.
        :param callable actor: Callable that does something with the instance.
        :param int attempts: Try this many times until :py:meth:`_wait_alive` passes.
        """

        for i in range(0, attempts):
            self.debug("Try action '{}', attempt #{} of {}".format(label, i + 1, attempts))

            try:
                actor()

                return self._wait_alive()

            except GlueError as exc:
                self.error('Failed to bring the guest alive in attempt #{}: {}'.format(i + 1, exc.message))
                self.warn('instance status: {}'.format(self._get_resource_status('servers', self._os_instance.id)))

                # If instance status is ACTIVE, it started but some additional check failed. We simply
                # cannot just run `actor` again because, from the OpenStack's point of view the instance
                # is already running, and `actor` would probably fail as, for example, one cannot "start"
                # ACTIVE instance. So, stop the instance to give actor leveled field.
                if self._check_resource_status('servers', self._os_instance.id, u'ACTIVE'):
                    # ACTIVE - shut down before another attempt
                    self._shutdown()

        raise GlueError('Failed to acquire living instance.')

    #
    # "Public" API
    #

    @property
    def supports_snapshots(self):
        return True

    def setup(self, variables=None, **kwargs):
        """
        Custom setup for Openstack guests. Add a resolvable openstack hostname in case there
        is none.

        :param dict variables: dictionary with GUEST_HOSTNAME and/or GUEST_DOMAINNAME keys
        """
        variables = variables or {}

        # workaround-openstack-hostname.yaml requires hostname and domainname.
        # If not set, create ones - some tests may depend on resolvable hostname.
        if 'GUEST_HOSTNAME' not in variables:
            variables['GUEST_HOSTNAME'] = re.sub(r'10\.(\d+)\.(\d+)\.(\d+)', r'host-\1-\2-\3', self.ip)

        if 'GUEST_DOMAINNAME' not in variables:
            variables['GUEST_DOMAINNAME'] = 'host.centralci.eng.rdu2.redhat.com'

        if 'IMAGE_NAME' not in variables:
            variables['IMAGE_NAME'] = self.image.name

        return super(OpenstackGuest, self).setup(variables=variables, **kwargs)

    def destroy(self):
        """
        The destroy function makes sure that assigned floating IP is freed, all snapshots are removed
        and the instance is deleted.
        """

        self._release_floating_ip()
        self._release_instance()
        self._release_snapshots()

    def create_snapshot(self, start_again=True):
        """
        Creates a snapshot from the current running image of the openstack instance.
        As snapshot name the instance name plus current date time is used.
        Example of a snapshot name: 'citool-176.13.42.52_2017-03-10_10-11:07:53'

        All created snapshots are deleted automatically during destruction.

        :rtype: OpenStackImage
        :returns: newly created image.
        """

        name = strftime('{}_%Y-%m-%d_%d-%H:%M:%S'.format(self.name), gmtime())
        self.debug("creating image snapshot named '{}'".format(name))

        # we need to shutdown the instance before creating snapshot
        self._shutdown()

        # create image
        image_id = self._call_api('instance.create_image', self._os_instance.create_image, name)
        image = OpenStackImage(self._module, name)
        self._snapshots.append(image)

        # we need to wait until the image is ready for usage
        self._wait_for_resource_status('snapshot reports ACTIVE', 'images', image_id, u'ACTIVE',
                                       timeout=self._module.option('activation-timeout'), tick=1)

        self.info("image snapshot '{}' created".format(name))

        if start_again is True:
            self._bring_alive('starting the instance after snapshot', self._start,
                              attempts=self._module.option('start-after-snapshot-attempts'))

        return image

    def restore_snapshot(self, snapshot):
        """
        Rebuilds server with the given snapshot image.

        :param snapshot: Either image name, or an :py:class:`OpenStackImage` instance.
        :rtype: OpenstackGuest
        :returns: server instance rebuilt from given image.
        """

        self.info("rebuilding server with snapshot '{}'".format(snapshot.name))

        try:
            self._shutdown()

        except GlueError as exc:
            # if it's not a timeout, re-raise
            if 'failed to pass within given time' not in exc.message:
                raise

            # We tried to shutdown the instance, and waiting for SHUTOFF ended up with a timeout.
            # As shutdown goes through the software - e.g. via initd/systemd - to shut the instance
            # down - it may be a sign of a totally broken software stack on the instance. As we cannot
            # force shutdown ("turn power off") - [1] - the only way out is to throw away this instance
            # and get a new one. Keep things like IP address and name, and avoid the rebuild by using
            # the snapshot as an initial image.
            # [1] https://blueprints.launchpad.net/nova/+spec/nova-api-force-stop-server
            self.warn('failed to shutdown - instance is probably broken beyond repair')

            label = 'provisioning replacement'

            def actor():
                if self._os_instance is not None:
                    self._release_instance()

                # No need to acquire name, NICs nor IP, these bits don't change. We simply ask for
                # a new instance with all these bits already acquired.
                self._acquire_instance(image=snapshot)
                self._assign_floating_ip()

                # Instance should be started by OS. `_bring_alive`, this function's caller, continues
                # with `_wait_alive` which is absolutely fine as it first checks for ACTIVE state,
                # and that's exactly what the instance should have when OS finishes its work.

        else:
            label = 'rebuilding the instance'

            def actor():
                self._rebuild(snapshot)

                # _rebuild leaves instance in the original state, SHUTDOWN
                self._start()

        self._bring_alive(label, actor, attempts=self._module.option('restore-snapshot-attempts'))

        self.info('rebuilt and alive')

        return self


class CIOpenstack(gluetool.Module):
    """
    This module manages Openstack guests. It provides a shared function
    `provision` to create given number of guests.

    When the module is destroyed it disassociates all associated floating IPs,
    deletes all created snapshots and removes all guests.

    The module provides reservation functionality. By using the 'reserve' option
    the module will create a file in the directory specified by
    the 'reserve-directory' option for each reserved machine. The created files
    are unique and their name is

        ${JOB_NAME}_${BUILD_ID}_INSTANCE_ID

    or just

        INSTANCE_ID

    if one of the environment variables JOB_NAME or BUILD_ID are not found. The
    INSTANCE_ID is replaced by the Openstack instance hash, which uniquely
    identifies the machine.

    The file will contain one record for each provisioned host with the following
    content:

        TIMESTAMP INSTANCE_ID IPv4_ADDRESS

    TIMESTAMP is a human readable reservation expiration time, e.g. 2017-04-13T03:05:09.

    INSTANCE_ID is a unique hash identifying the reserved machine.

    IPv4_ADDRESS is the floating IP address attached to the machine. It is
    provided only for reference, it is not being used by the module, but needs to be
    present.

    Moreover the module adds the TIMESTAMP on the instance itself into a file whose
    path is specified by the DEFAULT_REMOTE_RESERVE_FILE variable.

    The reserved machines cleanup is done by calling the module with '--cleanup'
    or '--cleanup-force' paramters.

    With forced cleanup all reserved machines will be destroyed.

    With ordinary cleanup, the module will use the highest reservation expiration time
    from the instance and the reservation file. If this time has passed, the machines
    will be destroyed. The cleanup will be also performed right away if the timestamp
    file is gone from the machine.
    """

    name = 'openstack'
    description = 'Provides Openstack guests'

    # pylint: disable=gluetool-option-has-no-default
    options = [
        ('Authentication options', {
            'api-version': {
                'help': 'Client API version (default: %(default)s)',
                'default': '2',
            },
            'auth-url': {
                'help': 'Auth URL'
            },
            'project-domain-name': {
                'help': 'Project domain name, required for OpenStack Identity API v3 authentication only',
            },
            'password': {
                'help': 'Password'
            },
            'project-name': {
                'help': 'Project/Tenant Name'
            },
            'username': {
                'help': 'Username to use for authentication'
            },
            'user-domain-name': {
                'help': 'User domain name, required for OpenStack Identity API v3 authentication only',
            },
        }),
        ('Common options', {
            'cleanup': {
                'help': 'Cleanup reserved machines which have expired timestamp',
                'action': 'store_true',
            },
            'cleanup-force': {
                'help': 'Force cleanup reserved machines which have expired timestamp',
                'action': 'store_true',
            },
            'flavor': {
                'help': 'Default flavor of machines (default: %(default)s)',
                'default': DEFAULT_FLAVOR,
            },
            'image': {
                'help': 'Force image name to be used, by default read it from openstack_image shared_function',
            },
            'ip-pool-name': {
                'help': 'Name of the floating ips pool name to use',
            },
            'keep': {
                'help': """Keep instance(s) running, do not destroy. No reservation records are created and it is
    expected from the user to cleanup the instance(s).""",
                'action': 'store_true',
            },
            'key-name': {
                'help': 'Name of the keypair to inject into instance',
            },
            'name-template': {
                'help': """
                        Template for guest names. It has access to the eval context, and ``GUEST_INDEX``
                        variable which represents the order number of the guest in the current pipeline
                        (default: %(default)s).
                        """,
                'default': DEFAULT_NAME_TEMPLATE
            },
            'network': {
                'help': 'Label of network to attach instance to',
            },
            'provision': {
                'help': 'Provision given number of guests',
                'metavar': 'COUNT',
                'type': int,
            },
            'setup-provisioned': {
                'help': "Setup guests after provisioning them. See 'guest-setup' module",
                'action': 'store_true'
            },
            'remove-all': {
                'help': 'DANGEROUS: Remove all instances and floating IPs',
                'action': 'store_true',
            },
            'reserve': {
                'help': 'Creates reservation records and keeps the instance(s) provisioned',
                'action': 'store_true',
            },
            'reserve-directory': {
                'help': 'Reservation records directory (default: %(default)s)',
                'metavar': 'PATH',
                'default': DEFAULT_RESERVE_DIR,
            },
            'reserve-time': {
                'help': 'Reservation time in hours (default: %(default)s)',
                'default': DEFAULT_RESERVE_TIME,
                'metavar': 'HOURS',
                'type': int,
            },
            'ssh-key': {
                'help': 'Path to SSH public key file'
            },
            'ssh-user': {
                'help': 'SSH username'
            },
            'user-data': {
                'help': """
                        User data to pass to OpenStack when requesting guests. If the value doesn't start
                        with '#cloud-config', it's considered a path and module will read the actual userdata from it.
                        """
            }
        }),
        ('Tenant options', {
            'arch': {
                'help': 'Architecture of instances provided by the tenant'
            }
        }),
        ('Glance options', {
            'glance.auth-url': {
                'help': 'Glance AUTH URL (default: value of ``--auth-url`` option)',
                'metavar': 'URL'
            },
            'glance.project-name': {
                'help': 'Glance project name (default: value of ``--project-name`` option)',
                'metavar': 'NAME'
            },
            'glance.username': {
                'help': 'Glance username (default: value of ``--username`` option)'
            },
            'glance.password': {
                'help': 'Glance password (default: value of ``--password`` option)'
            }
        }),
        ('Timeouts', {
            'acquire-timeout': {
                'help': 'Wait SECONDS for a guest to become acquire over network (default: %(default)s).',
                'type': int,
                'default': DEFAULT_ACQUIRE_TIMEOUT,
                'metavar': 'SECONDS'
            },
            'activation-timeout': {
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
                'help': 'Wait SECONDS for a guest to finish its booting process (default: %(default)s)',
                'type': int,
                'default': DEFAULT_BOOT_TIMEOUT,
                'metavar': 'SECONDS'
            },
            'shutdown-timeout': {
                'help': 'Wait SECONDS for a guest to finish its shutdown process (default: %(default)s)',
                'type': int,
                'default': DEFAULT_SHUTDOWN_TIMEOUT,
                'metavar': 'SECONDS'
            }
        }),
        ('Workarounds', {
            'degraded-services-map': {
                'help': 'Mapping of services which are allowed to be degraded while checking boot process status.'
            },
            'start-after-snapshot-attempts': {
                'help': """
                        When starting guest after taking its snapshot, try this many times before giving up
                        (default: %(default)s)
                        """,
                'type': int,
                'default': DEFAULT_START_AFTER_SNAPSHOT_ATTEMPTS
            },
            'restore-snapshot-attempts': {
                'help': """
                        When rebuilding guest to restore a snapshot, try this many times before giving up
                        (default: %(default)s)
                        """,
                'type': int,
                'default': DEFAULT_RESTORE_SNAPSHOT_ATTEMPTS
            }
        })
    ]

    required_options = (
        'auth-url', 'password', 'project-name', 'username', 'ssh-key',
        'arch'
    )
    shared_functions = ('openstack', 'provision', 'provisioner_capabilities')

    # connection handler
    nova = None

    # all openstack guests
    _all = []

    def __init__(self, *args, **kwargs):
        super(CIOpenstack, self).__init__(*args, **kwargs)

        # counter for guest instances, used in instance name, must be thread-safe
        self._guest_counter_lock = threading.Lock()
        self._guest_counter = 0

    @property
    def eval_context(self):
        __content__ = {  # noqa
            'OPENSTACK_GUESTS': """
                             List of `OpenstackGuest` object, which represents currently provisioned guests
                             from Openstack.
                             """
        }

        return {
            'OPENSTACK_GUESTS': self._all
        }

    @property
    def guest_count(self):
        """
        Returns current guest count.

        :return: Number of provisioned guests.
        """
        return self._guest_counter

    @property
    def acquire_guest_index(self):
        """
        Acquires and returns new index of the guest. Is thread-safe, uses locking.

        :return: Provisioned guest index.
        """
        with self._guest_counter_lock:
            self._guest_counter += 1

            return self._guest_counter

    @cached_property
    def user_data(self):
        user_data = self.option('user-data')

        if user_data is None:
            return None

        if not user_data.startswith('#cloud-config'):
            self.debug("loading userdata from '{}'".format(user_data))

            with open(normalize_path(user_data), 'r') as f:
                user_data = f.read()

        self.debug('userdata:\n{}'.format(user_data))
        return user_data

    @cached_property
    def degraded_services_map(self):
        if not self.option('degraded-services-map'):
            return []

        return load_yaml(self.option('degraded-services-map'), logger=self.logger)

    def _resource_not_found(self, resource, name, name_attr='name'):
        available = sorted([getattr(item, name_attr) for item in getattr(self.nova, resource).list()])
        raise GlueError("resource of type {} and value '{}' not found, available:\n{}".format(resource, name,
                                                                                              format_dict(available)))

    def _call_api(self, method_label, method, *args, **kwargs):
        return _call_api(self.logger, method_label, method, *args, **kwargs)

    def openstack(self):
        return self.nova

    def _get_reservation_file_name(self, guest):
        """
        Returns unique reservation file name. This is by default built from environment variables and
        has the form

            ${JOB_NAME}_${BUILD_ID}_INSTANCE_ID

        If any of the environment variables JOB_NAME or BUILD_ID are missing from the environment only
        INSTANCE_ID will be used, i.e.

            INSTANCE_ID
        """
        try:
            fname = "{}_{}_{}".format(os.environ['JOB_NAME'], os.environ['BUILD_ID'], guest.instance_id)
            self.debug('using JOB_NAME, BUILD_ID and instance id as reservation file name')
            return fname
        except KeyError:
            self.debug('using instance id as reservation file name')
            return guest.instance_id

    def _get_reservation_time(self):
        """
        Return reservation time string. We use the format defined by the TIME_FORMAT for storing the timestamp.
        """
        reserve_until = (datetime.now() + timedelta(hours=self.option('reserve-time'))).strftime(TIME_FORMAT)
        self.debug("reservation time until '{}'".format(reserve_until))
        return reserve_until

    def _create_reservation_directory(self):
        """
        Make sure that reservation directory exists. Will silently ignore if directory already exist.
        """
        self._reservation_directory = normalize_path(self.option('reserve-directory'))
        # make sure reservation directory exists
        try:
            os.makedirs(self._reservation_directory)
        except OSError as e:
            # be happy if someone already created the path
            if e.errno != errno.EEXIST:
                raise e

    def _reserve_guests(self):
        """
        Add reservation records to the shared reservation directory and the timestamp file on the guest.
        """
        # count reservation time
        reservation_time = self._get_reservation_time()
        self.info("reserving guests until '{}'".format(reservation_time))

        # go through all guests and write reservation files
        for guest in self._all:
            with open(os.path.join(self._reservation_directory, self._get_reservation_file_name(guest)), 'w') as f:
                # record guest details into the reservation file
                f.write('{} {} {}{}'.format(reservation_time,
                                            guest.instance_id,
                                            guest.ip,
                                            os.linesep))

            # record the reservation time to the remote reservation file
            guest.execute('echo {} > {}'.format(reservation_time, DEFAULT_REMOTE_RESERVE_FILE))

    def _cleanup_guest(self, handle):
        """
        Read one reservation record from the opened file identified by the file handle and destroy
        the instance if we need to cleanup the machine.

        Also remove invalid files if encountered. The machine will be destroyed also when the timestamp
        file is gone from the guest.

        :param file handle: An opened reservation file handle for reading.
        """
        try:
            # the instance floating will be resolved from the instance
            # we have it in the reservation file just for reference
            strtime, instance_id, _ = handle.readline().split()
            local_timestamp = datetime.strptime(strtime, TIME_FORMAT)
        except IOError as e:
            raise GlueError('error reading file: {}'.format(e))
        except ValueError as e:
            self.info("invalid format, caused error (file will be removed): '{}'".format(e))
            return True

        # we need to provide details required for network resolving
        details = {
            'network': self._get_network_ref(),
            'ip_pool_name': self.option('ip-pool-name'),
        }

        try:
            # init existing Openstack server from instance_id
            guest = OpenstackGuest(self, details=details, instance_id=instance_id)
        except NotFound:
            self.info("guest '{}' not found (file will be removed)".format(instance_id))
            return True

        if self.option('cleanup-force'):
            guest.info("destroying because of forced cleanup")
            guest.destroy()
            return True

        try:
            strtime = guest.execute("cat {}".format(DEFAULT_REMOTE_RESERVE_FILE)).stdout.rstrip()
            self.debug("read timestamp '{}' from remote file '{}'".format(strtime, DEFAULT_REMOTE_RESERVE_FILE))
            guest_timestamp = datetime.strptime(strtime, TIME_FORMAT)
        except GlueCommandError as exc:
            # remove the guest if the file is gone
            if 'No such file' in exc.output.stderr:
                guest.info("destroying because remote timestamp file is gone")
                guest.destroy()
                return True
            guest_timestamp = 0

        # use the largest timestamp
        timestamp = max([guest_timestamp, local_timestamp])

        # validate timestamp
        if datetime.now() > timestamp:
            guest.info("destroying because timestamp '{}' has been reached".format(timestamp))
            guest.destroy()
            return True

        guest.info("staying up because timestamp '{}' has not been reached".format(timestamp))
        return False

    def _cleanup(self):
        """
        Go through all reservation files and free expired machines. First look at the instance and use
        the remote reserve file for validation. If the machine is down, use the local timestamp.

        In case user requested forced cleanup, remove machine without timestamp validation
        """
        directory = normalize_path(self.option('reserve-directory'))
        for root, _, files in os.walk(directory):
            # we expect here that there are now subdirectories here
            if not files:
                self.info('no instance to cleanup, skipping')
                continue
            for filename in files:
                path = os.path.join(root, filename)
                self.debug("processing reserve file '{}'".format(path))
                with open(path) as f:
                    if self._cleanup_guest(f):
                        self.debug("removing reservation file '{}'".format(path))
                        os.unlink(path)

    def _provision_image(self, image):
        """
        Find what image the module should use for provisioning.

        Following images are used, if set, in this order:

            1. the ``image`` argument
            2. the ``image`` option - command-line overrides config file
            3. the ``image`` shared function.

        .. warning::

           Found image does not necessary mean there is such image in the OpenStack.
           It just represents the image module should use - the actual checks whether
           such image exists will come later.

        :rtype: OpenStackImage
        :returns: image to be used for provisioning, or ``None`` if the module cannot find
            the answer.
        """

        if not image:
            image = self.option('image') or self.shared('image')

            if image is None:
                raise GlueError('No image name specified')

        return OpenStackImage.factory(self, image)

    def _get_network_ref(self):
        # get network reference
        networks = self.option('network')
        if networks is not None:

            def _get_network_ref(network):
                # get network reference label
                try:
                    return self._call_api('networks.find', self.nova.networks.find, label=network)
                except (NotFound, BadRequest):
                    try:
                        return self._call_api('networks.find', self.nova.networks.find, id=network)
                    except NotFound:
                        # get network reference by id
                        self._resource_not_found('networks', network, name_attr='label')

            return [_get_network_ref(network) for network in networks.split(',')]

        return None

    def provision(self, environment, count=1, name=None, image=None, flavor=None, **kwargs):
        assert count >= 1, 'count needs to >= 1'

        self.info('provisioning guest for environment {}'.format(environment))

        image = self._provision_image(image)

        # get flavor reference
        flavor = flavor or self.option('flavor')
        try:
            flavor_ref = self._call_api('flavors.find', self.nova.flavors.find, name=flavor)
        except NotFound:
            self._resource_not_found('flavors', flavor)

        # create given number of guests
        guests = []
        for _ in range(count):
            if name:
                actual_name = name

            else:
                actual_name = gluetool.utils.render_template(
                    self.option('name-template'),
                    logger=self.logger,
                    **dict_update(
                        self.shared('eval_context'),
                        {
                            'GUEST_INDEX': self.acquire_guest_index
                        }
                    )
                )

            details = {
                'name': actual_name,
                'image': image,
                'flavor': flavor_ref,
                'network': self._get_network_ref(),
                'key_name': self.option('key-name'),
                'ip_pool_name': self.option('ip-pool-name'),
                'username': self.option('ssh-user'),
                'key': self.option('ssh-key'),
                'user_data': self.user_data
            }

            log_dict(self.debug, 'creating guest with following details', details)

            guest = OpenstackGuest(self, details=details, environment=environment.clone(compose=image.name))

            self._all.append(guest)
            guests.append(guest)

        self.debug('created {} guests, waiting for them to become alive'.format(count))

        for guest in guests:
            # instance is already started by OpenStack, check its status before moving on
            guest._wait_alive()

        if self.option('reserve'):
            self._reserve_guests()

        self.info("created {} instance(s) with flavor '{}' from image '{}'".format(count, flavor, image.name))
        log_dict(self.info, 'provisioned guests', guests)

        return guests

    provision.__doc__ = """
        Provision (multiple) OpenStack guests. The name of the guests is created from the ``name`` parameter
        plus the floating IPv4 address of the guest.

        Image is defined by these options (in this order, first one wins):

            - ``image`` parameter of this function
            - command-line option ``--image``
            - configuration file, ``image`` option
            - ``openstack_image`` shared function

        :param tuple environment: description of the envronment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.

            .. warning::

               Currently, this parameter is ignored. It has been introduced this early to decouple
               users of this module, and its actual use will follow in the near future. But, still,
               it has to be an object compatible with the protocol, i.e. it must provide necessary
               fields.

        :param int count: number of openstack guests to create
        :param str name: if set, it is used to name the instances, otherwise the template set by
            ``name-template`` is used.
        :param str image: image to use (default: see above)
        :param str flavor: flavor to use for the instance (default: ``{default_flavor}``)
        """.format(default_flavor=DEFAULT_FLAVOR)

    def provisioner_capabilities(self):
        """
        Return description of OpenStack provisioner capabilities.

        Follows :doc:`Provisioner Capabilities Protocol </protocols/provisioner-capabilities>`.
        """

        return ProvisionerCapabilities(
            available_arches=[
                self.option('arch')
            ]
        )

    def destroy(self, failure=None):
        if not self._all:
            return

        if self.option('keep'):
            self.info('keeping guests provisioned, important note: NO AUTOMATIC CLEANUP')
            return

        if self.option('reserve') and not self.option('cleanup-force'):
            self.info("keeping guests reserved, expecting regular cleanup via '--cleanup' option")
            return

        for instance in self._all:
            instance.destroy()
        self._all = []

        self.info('successfully removed all guests')

    def sanity(self):
        if self.option('reserve'):
            self._create_reservation_directory()

    def execute(self):
        api_version = self.option('api-version')
        auth_url = self.option('auth-url')
        project_domain_name = self.option('project-domain-name')
        project_name = self.option('project-name')
        password = self.option('password')
        username = self.option('username')
        user_domain_name = self.option('user-domain-name')
        key_name = self.option('key-name')
        provision_count = self.option('provision')
        cleanup = self.option('cleanup')
        cleanup_force = self.option('cleanup-force')

        # If domain name is specified, we use for authentication keystone client, which supports v3 Client API.
        # This was required for newer Openstack versions.
        #
        # https://stackoverflow.com/questions/33698861/openstack-novaclient-python-api-not-working
        if project_domain_name and user_domain_name:
            self.info('using OpenStack Identity API v3 for authentication')
            auth = keystone_identity.Password(
                auth_url=auth_url,
                username=username,
                password=password,
                project_domain_name=project_domain_name,
                project_name=project_name,
                user_domain_name=user_domain_name)

            self.nova = client.Client(api_version, session=keystone_session.Session(auth=auth))

        # Use v2 Client API for authentication
        else:
            # connect to openstack instance
            self.nova = client.Client(api_version,
                                      auth_url=auth_url,
                                      username=username,
                                      password=password,
                                      project_name=project_name)

        # test connection
        try:
            self._call_api('servers.list', self.nova.servers.list)
        except Unauthorized:
            raise GlueError('invalid openstack credentials')
        self.info("connected to '{}' with user '{}', project '{}'".format(auth_url,
                                                                          username,
                                                                          project_name))

        if self.option('remove-all'):
            self.info('This will remove all instances and floating IPs! Do you really want to continue? Type YES now.')
            if six.moves.input() == 'YES':
                # remove all instances
                for server in self._call_api('servers.list', self.nova.servers.list):
                    self.info("removing instance '{}'".format(server.name))
                    self._call_api('instance.delete', server.delete)
                # remove all floating ips
                for floating_ip in self._call_api('floating_ips.list', self.nova.floating_ips.list):
                    self.info("removing ip '{}'".format(floating_ip.ip))
                    self._call_api('floating_ip.delete', floating_ip.delete)
            else:
                self.info('cowardly skipping removal of all resources')

        # check if key name valid
        self._call_api('keypairs.find', self.nova.keypairs.find, name=key_name)

        # provision given number of guests right away
        if provision_count:
            # `provision` ignores the content of `environment`, but it still must be a valid
            # environment object. We fill its properties as best as we can for direct provisioning.
            # Hopefully, `openstack` will soon accept environment specification instead of this.
            environment = TestingEnvironment(arch=self.option('arch'), compose=self.option('image'))

            guests = self.provision(environment, count=provision_count)

            if self.option('setup-provisioned'):
                for guest in guests:
                    guest.setup()

        # run cleanup if requested
        if cleanup or cleanup_force:
            self._cleanup()
