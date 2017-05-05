import errno
import os
import re
from time import gmtime, strftime
from datetime import datetime, timedelta

from novaclient import client
from novaclient.exceptions import NotFound, Unauthorized
from retrying import retry

from libci import Module, CIError, CICommandError
from libci.guest import NetworkedGuest
from libci.utils import format_dict, cached_property

DEFAULT_FLAVOR = 'm1.small'
DEFAULT_NAME = 'citool'
DEFAULT_RESERVE_DIR = '~/openstack-reservations'
DEFAULT_REMOTE_RESERVE_FILE = '~/.openstack-reservation'
DEFAULT_RESERVE_TIME = 24
MAX_SERVER_ACTIVATION = 240
MAX_SERVER_SHUTDOWN = 60
MAX_IMAGE_ACTIVATION = 60
DEFAULT_SSH_OPTIONS = ['UserKnownHostsFile=/dev/null', 'StrictHostKeyChecking=no']
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


class OpenstackGuest(NetworkedGuest):
    """
    Implements Openstack Network Guest with snapshots support.
    """
    @retry(stop_max_attempt_number=10, wait_fixed=1000)
    def _assign_ip(self):
        """
        The assignment of IP can fail if done too early. So retry it 10 times
        to be sure that there is some other issue.
        """
        self._instance.add_floating_ip(self._ip)

    def __init__(self, module, details=None, instance_id=None):
        self._snapshots = []
        self._nova = module.nova
        details = details or {}

        #
        # Create a new instance
        #
        if instance_id is None:
            assert details is not None, 'no details passed to OpenstackGuest constructor'

            # get an floating IP from a random available pool
            self._ip = self._nova.floating_ips.create(details['ip_pool_name'])

            # create instance name with floating IP and optionally add JOB_NAME and BUILD_ID
            name = [
                details['name'],
                self._ip.ip
            ]
            if 'JOB_NAME' in os.environ:
                name.append('{}-{}'.format(os.environ['JOB_NAME'], os.environ['BUILD_ID']))
            name = '-'.join(name)

            # complete userdata - use our default
            # create openstack instance
            self._instance = self._nova.servers.create(name=name,
                                                       flavor=details['flavor'],
                                                       image=details['image'],
                                                       network=details['network'],
                                                       key_name=details['key_name'],
                                                       userdata=details['user_data'])

            self._assign_ip()

        #
        # Intialize from an existing instance
        #
        else:
            self._instance = self._nova.servers.find(id=instance_id)
            self._ip = self._nova.floating_ips.find(instance_id=instance_id)
            name = self._instance.to_dict()['name']
            details.update({
                'username': module.option('ssh-user'),
                'key': module.option('ssh-key')
            })

        super(OpenstackGuest, self).__init__(module,
                                             str(self._ip.ip),
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
            raise CIError("Cannot find image by its ID '{}'".format(img_id))

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
            variables['GUEST_HOSTNAME'] = re.sub(r'10\.(\d+)\.(\d+)\.(\d+)', r'host-\1-\2-\3', str(self._ip.ip))

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
        try:
            self._ip.delete()
            self.verbose("removed floating IP '{}'".format(self._ip.ip))
        except NotFound:
            self.debug('associated floating IP already removed - skipping')
        self._remove_snapshots()
        try:
            self._instance.delete()
            self.verbose("removed instance '{}'".format(self._instance.name))
        except NotFound:
            self.debug('instance already deleted - skipping')

    def _check_resource_status(self, resource, rid, status):
        """
        Check if resource with given id is in expected status.

        param: str resource: resource type (images, servers, etc.)
        param: unicode id: ID of the resource to check
        param: unicode status: expected status of the resource in unicode

        """
        obj = getattr(self._nova, resource).find(id=rid)
        self.debug('{} resource status: {}'.format(resource, obj.status))
        if obj.status != status:
            raise CIError("{} resource has invalid status '{}', expected '{}'".format(resource, obj.status, status))

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
        self._instance.start()
        self.wait_alive(timeout=self._module.option('activation-time'), tick=1)
        self.debug("server '{}' is up now".format(self.name))

        return name

    def restore_snapshot(self, snapshot):
        """
        Rebuilds server with the given snapshot image.

        param: image instance
        :returns: server instance rebuilt from given image.
        """

        self.info("rebuilding server with snapshot '{}'".format(snapshot))
        self._instance.rebuild(self._module.get_image_ref(snapshot))
        self.wait_alive(timeout=self._module.option('activation-time'), tick=1)
        self.info("instance rebuilt and is up now")

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
        return str(self._ip.ip)

    @cached_property
    def instance_id(self):
        """
        Provides instance ID as a string.

        :returns: string representation of instance ID
        """
        return str(self._instance.id)


class CIOpenstack(Module):
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
    options = {
        'api-version': {
            'help': 'API version (default: 2)',
            'default': '2'
        },
        'activation-time': {
            'help': "Machines maximum activation time before timeout in \
seconds (default: {})".format(MAX_SERVER_ACTIVATION),
            'type': int,
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
            'help': """User data to pass to OpenStack when requesting guests. If the value doesn't start
with '#cloud-config', it's considered a path and module will read the actual userdata from it."""
        }
    }
    required_options = ['auth-url', 'password', 'project-name', 'username', 'ssh-key', 'ip-pool-name']
    shared_functions = ['provision']

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

            with open(user_data, 'r') as f:
                user_data = f.read()

        self.debug('userdata:\n{}'.format(user_data))
        return user_data

    def _resource_not_found(self, resource, name):
        available = [item.name for item in getattr(self.nova, resource).list()]
        raise CIError("resource of type {} and value '{}' not found, available:\n{}".format(resource, name,
                                                                                            format_dict(available)))

    def get_image_ref(self, name):
        self.debug("get image reference for '{}'".format(name))

        image_refs = self.nova.images.findall(name=name)
        if not image_refs:
            self._resource_not_found('images', name)

        for image_ref in image_refs:
            self.debug('name: {}, status: {}'.format(image_ref.name, image_ref.status))
            if image_ref.status == u'ACTIVE':
                return image_ref

        raise CIError("Multiple images found for '{}', and none of them is active".format(name))

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
            raise CIError('error reading file: {}'.format(e))
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
        except CICommandError as exc:
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
        """
        Provision multiple openstack guests from the given image name. The flavor is by
        default {}. The name of the guests is created from the name parameter plus the floating
        IPv4 address.

        :param int count: number of openstack guests to create
        :param str name: box name, by default DEFAULT_NAME
        :param str image: image to use, by default taken from cmdline/config or `openstack_image` shared function
        :param str flavor: flavor to use for the instance, by default DEFAULT_FLAVOR
        """.format(DEFAULT_FLAVOR)
        assert count >= 1, 'count needs to >= 1'

        # read image name in this priority order:
        # 1. from this function
        # 2. from image option
        # 3. from image option from configuration file
        # 4. from openstack_image shared function
        if image is None:
            image = self.option('image') or self.shared('openstack_image')
            if image is None:
                raise CIError('no image name specified')

        # get image reference
        image_ref = self.get_image_ref(image)

        # get flavor reference
        flavor = flavor or self.option('flavor')
        try:
            flavor_ref = self.nova.flavors.find(name=flavor)
        except NotFound:
            self._resource_not_found('flavors', flavor)

        # get network reference
        network = self.option('network')
        if network is not None:
            # get flavor reference
            try:
                network_ref = self.nova.networks.find(label=network)
            except NotFound:
                self._resource_not_found('network', network)
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
            guest.wait_alive(timeout=self.option('activation-time'), tick=1)

        if self.option('reserve'):
            self._reserve_guests()

        self.info("created {} instance(s) with flavor '{}' from image '{}'".format(count, flavor, image))

        return guests

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
            raise CIError('invalid openstack credentials')
        self.info("connected to '{}' with user '{}', project '{}'".format(auth_url,
                                                                          username,
                                                                          project_name))

        # check if key name valid
        self.nova.keypairs.find(name=key_name)

        # provision given number of guests right away
        if provision_count:
            self.provision(provision_count)

        # run cleanup if requested
        if cleanup or cleanup_force:
            self._cleanup()
