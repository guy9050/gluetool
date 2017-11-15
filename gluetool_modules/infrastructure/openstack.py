import errno
import gzip
import os
import re
from time import gmtime, strftime
from datetime import datetime, timedelta
from retrying import retry

import novaclient.exceptions
from novaclient import client
from novaclient.exceptions import BadRequest, NotFound, Unauthorized

import gluetool
from gluetool import GlueError, GlueCommandError
from gluetool.log import format_dict
from gluetool.utils import cached_property
from libci.guest import NetworkedGuest

DEFAULT_FLAVOR = 'm1.small'
DEFAULT_NAME = 'citool'
DEFAULT_RESERVE_DIR = '~/openstack-reservations'
DEFAULT_REMOTE_RESERVE_FILE = '~/.openstack-reservation'
DEFAULT_RESERVE_TIME = 24

DEFAULT_ACTIVATION_TIMEOUT = 240
ACTIVATION_TICK = 5
DEFAULT_ECHO_TIMEOUT = 240
ECHO_TICK = 10
DEFAULT_BOOT_TIMEOUT = 240
BOOT_TICK = 10

DEFAULT_START_AFTER_SNAPSHOT_ATTEMPTS = 3
DEFAULT_RESTORE_SNAPSHOT_ATTEMPTS = 3

MAX_SERVER_SHUTDOWN = 60
MAX_IMAGE_ACTIVATION = 60
DEFAULT_SSH_OPTIONS = ['UserKnownHostsFile=/dev/null', 'StrictHostKeyChecking=no']
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


class OpenstackGuest(NetworkedGuest):
    """
    Implements Openstack Network Guest with snapshots support.
    """

    ALLOW_DEGRADED = ('cloud-config.service',)

    @staticmethod
    def _acquire_os_resource(resource, logger, tick, func, *args, **kwargs):
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
        :param callable func: Function which, when called, will acquire the resource.
        :param tuple args: Positional arguments for ``func``.
        :param dict kwargs: Keyword arguments for ``func``.
        """

        def _ask():
            try:
                return func(*args, **kwargs)

            except novaclient.exceptions.Forbidden as exc:
                if not exc.message.startswith('Quota exceeded'):
                    raise GlueError('Failed to acquire {}: {}'.format(resource, exc.message))

                # Original message "Quota exceeded for cores: Requested 8, but already used 77 of 80 cores" is good
                # enough for public use, we just add a bit of sugar to let user know we're working on it.
                logger.info('{}. Will try again in a moment.'.format(exc.message))

                # let wait() know we need to try again
                return False

            except novaclient.exceptions.BadRequest as exc:
                # Handle floating IP not yet available for assignment
                if not exc.message.startswith('Instance network is not ready yet'):
                    raise GlueError('Failed to acquire {}: {}'.format(resource, exc.message))

                logger.info('{}. Will try again in a moment.'.format(exc.message))

                # let wait() know we need to try again
                return False

        return gluetool.utils.wait('acquire {} from OpenStack'.format(resource),
                                   _ask,
                                   logger=logger,
                                   tick=tick)

    def _check_resource_status(self, resource, rid, status):
        """
        Check whether the resource with given ID is in expected state.

        param: str resource: Resource type (``images``, ``servers``, etc.)
        param: unicode id: ID of the resource to check.
        param: unicode status: Expected status of the resource. Note the ``unicode`` type.
        """

        obj = getattr(self._nova, resource).find(id=rid)
        self.debug('{} resource status: {}'.format(resource, obj.status))
        if obj.status != status:
            raise GlueError("{} resource has invalid status '{}', expected '{}'".format(resource, obj.status, status))

    def _assign_floating_ip(self, floating_ip):
        """
        The add_floating_ip returns an instance of novaclient.base.TupleWithMeta
        https://docs.openstack.org/python-novaclient/latest/reference/api/novaclient.v2.servers.html

        :param nova.novaclient.v2.floating_ips.FloatingIP floating_ip: floating IP to assign
        :returns: True if floating IP successfully assigned, False otherwise
        """
        if isinstance(self._instance.add_floating_ip(floating_ip), novaclient.base.TupleWithMeta):
            return True
        return False

    def __init__(self, module, details=None, instance_id=None):
        self._snapshots = []
        self._nova = module.nova
        details = details or {}

        #
        # Create a new instance
        #
        if instance_id is None:
            assert details is not None, 'no details passed to OpenstackGuest constructor'

            # get an floating IP from a random available pool, tick for 30s before retrying
            # pylint: disable=line-too-long
            self._floating_ip = OpenstackGuest._acquire_os_resource('floating IP', module.logger, 30, self._nova.floating_ips.create,
                                                                    details['ip_pool_name'])

            # add additional network if specified
            nics = [{'net-id': network.id} for network in details['network']] if details.get('network', None) else []

            # create instance name with floating IP and optionally add JOB_NAME and BUILD_ID
            name = [
                details['name'],
                self.floating_ip
            ]
            if 'JOB_NAME' in os.environ:
                name.append('{}-{}'.format(os.environ['JOB_NAME'], os.environ['BUILD_ID']))
            name = '-'.join(name)

            # complete userdata - use our default
            # create openstack instance, tick for 30s before retrying
            # pylint: disable=line-too-long
            self._instance = OpenstackGuest._acquire_os_resource('instance', module.logger, 30, self._nova.servers.create,
                                                                 name=name,
                                                                 flavor=details['flavor'],
                                                                 image=details['image'],
                                                                 nics=nics,
                                                                 key_name=details['key_name'],
                                                                 userdata=details['user_data'])

            # the assignment of IP can fail if done too early. So retry if needed.
            # to be sure that we do not hit this. Also retrying should improve a bit
            # situation with shorter outages happening regularly on Openstack.
            # pylint: disable=line-too-long
            OpenstackGuest._acquire_os_resource('IP assignment', module.logger, 1, self._assign_floating_ip, self._floating_ip)  # Ignore PEP8Bear

        #
        # Intialize from an existing instance
        #
        else:
            self._instance = self._nova.servers.find(id=instance_id)
            self._floating_ip = self._nova.floating_ips.find(instance_id=instance_id)
            name = self._instance.to_dict()['name']
            details.update({
                'username': module.option('ssh-user'),
                'key': module.option('ssh-key')
            })

        super(OpenstackGuest, self).__init__(module,
                                             self.floating_ip,
                                             name=name,
                                             username=details['username'],
                                             key=details['key'],
                                             options=DEFAULT_SSH_OPTIONS)

    @cached_property
    def _image(self):
        assert self._instance is not None

        img_id = self._instance.image['id']

        try:
            return self._nova.images.findall(id=img_id)[0]

        except IndexError:
            raise GlueError("Cannot find image by its ID '{}'".format(img_id))

    def setup(self, variables=None, **kwargs):
        # pylint: disable=arguments-differ
        """
        Custom setup for Openstack guests. Add a resolvable openstack hostname in case there
        is none.

        :param dict variables: dictionary with GUEST_HOSTNAME and/or GUEST_DOMAINNAME keys
        """
        variables = variables or {}

        # workaround-openstack-hostname.yaml requires hostname and domainname.
        # If not set, create ones - some tests may depend on resolvable hostname.
        if 'GUEST_HOSTNAME' not in variables:
            variables['GUEST_HOSTNAME'] = re.sub(r'10\.(\d+)\.(\d+)\.(\d+)', r'host-\1-\2-\3', self.floating_ip)

        if 'GUEST_DOMAINNAME' not in variables:
            variables['GUEST_DOMAINNAME'] = 'host.centralci.eng.rdu2.redhat.com'

        if 'IMAGE_NAME' not in variables:
            variables['IMAGE_NAME'] = self._image.to_dict()['name']

        super(OpenstackGuest, self).setup(variables=variables, **kwargs)

    def destroy(self):
        """
        The destroy function makes sure that assigned floating IP is freed, all snapshots are removed
        and the instance is deleted.
        """

        # save console log, if possible
        try:
            filename = 'console-{}-{}.log.gz'.format(self.name, self.instance_id)

            self.debug("storing console output in '{}'".format(filename))

            console = self._instance.get_console_output()

            if console:
                console = console.encode('utf-8', 'replace')

            else:
                # Some servers may return empty console output. Observed with rhel-7.1-server-x86_64-released image
                self.warn('empty console output')

                console = '<Server returned empty console output>'

            with gzip.open(filename, 'wb') as f:
                f.write(console)
                f.flush()

        # pylint: disable=broad-except
        except Exception as exc:
            self.warn('Failed to store console output in the file: {}'.format(str(exc)), sentry=True)

        try:
            self._floating_ip.delete()
            self.verbose("removed floating IP '{}'".format(self.floating_ip))
        except NotFound:
            self.debug('associated floating IP already removed - skipping')
        self._remove_snapshots()
        try:
            self._instance.delete()
            self.verbose("removed instance '{}'".format(self._instance.name))
        except NotFound:
            self.debug('instance already deleted - skipping')

    def _wait_alive(self):
        """
        "Wait alive" helper - we're using the same options when calling guest.wait_alive, let's
        put the call in a helper method.

        """

        try:
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

            actor()

            try:
                return self._wait_alive()

            except GlueError as exc:
                self.error('Failed to bring the guest alive in attempt #{}: {}'.format(i + 1, exc.message))
                self.warn('instance status: {}'.format(self._instance.status))

        raise GlueError('Failed to acquire living instance.')

    def create_snapshot(self):
        """
        Creates a snapshot from the current running image of the openstack instance.
        As snapshot name the instance name plus current date time is used.
        Example of a snapshot name: 'citool-176.13.42.52_2017-03-10_10-11:07:53'

        All created snapshots are deleted automatically during destruction.

        :returns: created image id
        """
        name = strftime('{}_%Y-%m-%d_%d-%H:%M:%S'.format(self.name), gmtime())
        self.debug("creating image snapshot named '{}'".format(name))

        # stop instance
        self._instance.stop()

        # we need to shutdown the instance before creating snapshot
        # note: we are calling here the parametrized retry decorator
        retry(stop_max_attempt_number=MAX_SERVER_SHUTDOWN,
              wait_fixed=1000)(self._check_resource_status)('servers', self._instance.id, u'SHUTOFF')
        self.debug("server '{}' powered off".format(self.name))

        # create image
        image_id = self._instance.create_image(name)
        self._snapshots.append(image_id)

        # we need to wait until the image is ready for usage
        # note: we are calling here the parametrized retry decorator
        retry(stop_max_attempt_number=MAX_IMAGE_ACTIVATION,
              wait_fixed=1000)(self._check_resource_status)('images', image_id, u'ACTIVE')
        self.info("image snapshot '{}' created".format(name))

        # start instance
        self._bring_alive('starting the instance', self._instance.start,
                          attempts=self._module.option('start-after-snapshot-attempts'))
        self.debug('started and alive')

        return name

    def restore_snapshot(self, snapshot):
        """
        Rebuilds server with the given snapshot image.

        param: image instance
        :returns: server instance rebuilt from given image.
        """

        self.info("rebuilding server with snapshot '{}'".format(snapshot))

        def _rebuild():
            self._instance.rebuild(self._module.get_image_ref(snapshot))

        self._bring_alive('rebuilding the instance from a snapshot', _rebuild,
                          attempts=self._module.option('restore-snapshot-attempts'))
        self.info('rebuilt and alive')

        return self

    def _remove_snapshots(self):
        """
        Removes all created snapshots.
        """
        count = len(self._snapshots)
        for image_id in self._snapshots:
            image = self._module.nova.images.find(id=image_id)
            image.delete()
            self.debug("removed image with id '{}'".format(image_id))
        if count > 0:
            self.verbose('removed all {} snapshots'.format(count))
        self._snapshots = []

    def supports_snapshots(self):
        return True

    @cached_property
    def floating_ip(self):
        """
        Property provides associated floating IP address as a string.

        :returns: floating IP address of the guest
        """
        return str(self._floating_ip.ip)

    @cached_property
    def instance_id(self):
        """
        Provides instance ID as a string.

        :returns: string representation of instance ID
        """
        return str(self._instance.id)


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
    options = [
        ('Common options', {
            'api-version': {
                'help': 'API version (default: 2)',
                'default': '2'
            },
            'auth-url': {
                'help': 'Auth URL'
            },
            'cleanup': {
                'help': 'Cleanup reserved machines which have expired timestamp',
                'action': 'store_true',
            },
            'cleanup-force': {
                'help': 'Force cleanup reserved machines which have expired timestamp',
                'action': 'store_true',
            },
            'flavor': {
                'help': 'Default flavor of machines (default: {})'.format(DEFAULT_FLAVOR),
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
            'network': {
                'help': 'Label of network to attach instance to',
            },
            'password': {
                'help': 'Password'
            },
            'project-name': {
                'help': 'Project/Tenant Name'
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
            'reserve': {
                'help': 'Creates reservation records and keeps the instance(s) provisioned',
                'action': 'store_true',
            },
            'reserve-directory': {
                'help': 'Reservation records directory (default: {})'.format(DEFAULT_RESERVE_DIR),
                'metavar': 'PATH',
                'default': DEFAULT_RESERVE_DIR,
            },
            'reserve-time': {
                'help': 'Reservation time in hours (default: {})'.format(DEFAULT_RESERVE_TIME),
                'default': DEFAULT_RESERVE_TIME,
                'metavar': 'HOURS',
                'type': int,
            },
            'username': {
                'help': 'Username to used for authentication'
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
        ('Timeouts', {
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
                'help': 'Wait SECOND for a guest to finish its booting process (default: {})'.format(DEFAULT_BOOT_TIMEOUT),
                'type': int,
                'default': DEFAULT_BOOT_TIMEOUT,
                'metavar': 'SECONDS'
            }
        }),
        ('Workarounds', {
            'start-after-snapshot-attempts': {
                # pylint: disable=line-too-long
                'help': 'When starting guest after taking its snapshot, try this many times before giving up (default: {})'.format(DEFAULT_START_AFTER_SNAPSHOT_ATTEMPTS),
                'type': int,
                'default': DEFAULT_START_AFTER_SNAPSHOT_ATTEMPTS
            },
            'restore-snapshot-attempts': {
                # pylint: disable=line-too-long
                'help': 'When rebuilding guest to restore a snapshot, try this many times before giving up (default: {})'.format(DEFAULT_RESTORE_SNAPSHOT_ATTEMPTS),
                'type': int,
                'default': DEFAULT_RESTORE_SNAPSHOT_ATTEMPTS
            }
        })
    ]

    required_options = ['auth-url', 'password', 'project-name', 'username', 'ssh-key', 'ip-pool-name']
    shared_functions = ('openstack', 'provision')

    # connection handler
    nova = None

    # all openstack guests
    _all = []

    @cached_property
    def user_data(self):
        user_data = self.option('user-data')

        if user_data is None:
            return None

        if not user_data.startswith('#cloud-config'):
            self.debug("loading userdata from '{}'".format(user_data))

            with open(os.path.expanduser(user_data), 'r') as f:
                user_data = f.read()

        self.debug('userdata:\n{}'.format(user_data))
        return user_data

    def _resource_not_found(self, resource, name, name_attr='name'):
        available = sorted([getattr(item, name_attr) for item in getattr(self.nova, resource).list()])
        raise GlueError("resource of type {} and value '{}' not found, available:\n{}".format(resource, name,
                                                                                              format_dict(available)))

    def openstack(self):
        return self.nova

    def get_image_ref(self, name):
        self.debug("get image reference for '{}'".format(name))

        image_refs = self.nova.images.findall(name=name)
        if not image_refs:
            self._resource_not_found('images', name)

        for image_ref in image_refs:
            self.debug('name: {}, status: {}'.format(image_ref.name, image_ref.status))
            if image_ref.status == u'ACTIVE':
                return image_ref

        raise GlueError("Multiple images found for '{}', and none of them is active".format(name))

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
        self._reservation_directory = os.path.expanduser(self.option('reserve-directory'))
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
                                            guest.floating_ip,
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

        try:
            # init existing Openstack server from instance_id
            guest = OpenstackGuest(self, instance_id=instance_id)
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
        directory = os.path.expanduser(self.option('reserve-directory'))
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

    def provision(self, count=1, name=DEFAULT_NAME, image=None, flavor=None):
        assert count >= 1, 'count needs to >= 1'

        # read image name in this priority order:
        # 1. from this function
        # 2. from image option
        # 3. from image option from configuration file
        # 4. from openstack_image shared function
        if image is None:
            image = self.option('image') or self.shared('image')
            if image is None:
                raise GlueError('no image name specified')

        # get image reference
        image_ref = self.get_image_ref(image)

        # get flavor reference
        flavor = flavor or self.option('flavor')
        try:
            flavor_ref = self.nova.flavors.find(name=flavor)
        except NotFound:
            self._resource_not_found('flavors', flavor)

        # get network reference
        networks = self.option('network')
        if networks is not None:

            def _get_network_ref(network):
                # get network reference label
                try:
                    return self.nova.networks.find(label=network)
                except (NotFound, BadRequest):
                    try:
                        return self.nova.networks.find(id=network)
                    except NotFound:
                        # get network reference by id
                        self._resource_not_found('networks', network, name_attr='label')

            network_ref = [_get_network_ref(network) for network in networks.split(',')]
        else:
            network_ref = None

        # create given number of guests
        guests = []
        for _ in range(count):
            details = {
                'name': name,
                'image': image_ref,
                'flavor': flavor_ref,
                'network': network_ref,
                'key_name': self.option('key-name'),
                'ip_pool_name': self.option('ip-pool-name'),
                'username': self.option('ssh-user'),
                'key': self.option('ssh-key'),
                'user_data': self.user_data
            }

            self.verbose('creating guest with following details\n{}'.format(format_dict(details)))
            guest = OpenstackGuest(self, details=details)

            self._all.append(guest)
            guests.append(guest)

        self.debug('created {} guests, waiting for them to become ACTIVE'.format(count))

        for guest in guests:
            # pylint: disable=protected-access
            guest._wait_alive()

        if self.option('reserve'):
            self._reserve_guests()

        self.info("created {} instance(s) with flavor '{}' from image '{}'".format(count, flavor, image))

        return guests

    provision.__doc__ = """
        Provision (multiple) OpenStack guests. The name of the guests is created from the ``name`` parameter
        plus the floating IPv4 address of the guest.

        Image is defined by these options (in this order, first one wins):

            - ``image`` parameter of this function
            - command-line option ``--image``
            - configuration file, ``image`` option
            - ``openstack_image`` shared function

        :param int count: number of openstack guests to create
        :param str name: box name (default: {default_name})
        :param str image: image to use (default: see above)
        :param str flavor: flavor to use for the instance (default: ``{default_flavor}``)
        """.format(default_name=DEFAULT_NAME, default_flavor=DEFAULT_FLAVOR)

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

        self.info('successfully removed all guests')

    def sanity(self):
        if self.option('reserve'):
            self._create_reservation_directory()

    def execute(self):
        api_version = self.option('api-version')
        auth_url = self.option('auth-url')
        project_name = self.option('project-name')
        password = self.option('password')
        username = self.option('username')
        key_name = self.option('key-name')
        provision_count = self.option('provision')
        cleanup = self.option('cleanup')
        cleanup_force = self.option('cleanup-force')

        # connect to openstack instance
        self.nova = client.Client(api_version,
                                  auth_url=auth_url,
                                  username=username,
                                  password=password,
                                  project_name=project_name)

        # test connection
        try:
            self.nova.servers.list()
        except Unauthorized:
            raise GlueError('invalid openstack credentials')
        self.info("connected to '{}' with user '{}', project '{}'".format(auth_url,
                                                                          username,
                                                                          project_name))

        # check if key name valid
        self.nova.keypairs.find(name=key_name)

        # provision given number of guests right away
        if provision_count:
            guests = self.provision(provision_count)

            if self.option('setup-provisioned'):
                for guest in guests:
                    guest.setup()

        # run cleanup if requested
        if cleanup or cleanup_force:
            self._cleanup()
