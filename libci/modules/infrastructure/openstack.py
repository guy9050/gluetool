import re
from time import gmtime, strftime

from novaclient import client
from novaclient.exceptions import NotFound, Unauthorized
from retrying import retry

from libci import Module, CIError
from libci.guest import NetworkedGuest
from libci.utils import format_dict, cached_property

DEFAULT_FLAVOR = 'm1.small'
DEFAULT_NAME = 'citool'
MAX_SERVER_ACTIVATION = 240
MAX_SERVER_SHUTDOWN = 60
MAX_IMAGE_ACTIVATION = 60
DEFAULT_SSH_OPTIONS = ['UserKnownHostsFile=/dev/null', 'StrictHostKeyChecking=no']


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

    def __init__(self, module, details):
        self._instance = None
        self._ip = None
        self._snapshots = []
        self._nova = module.nova

        # get an floating IP from a random available pool
        self._ip = self._nova.floating_ips.create(details['ip_pool_name'])

        # complete userdata - use our default
        # create openstack instance
        name = '{}-{}'.format(details['name'], self._ip.ip)
        self._instance = self._nova.servers.create(name=name,
                                                   flavor=details['flavor'],
                                                   image=details['image'],
                                                   network=details['network'],
                                                   key_name=details['key_name'],
                                                   userdata=details['user_data'])

        self._assign_ip()

        super(OpenstackGuest, self).__init__(module,
                                             str(self._ip.ip),
                                             name=name,
                                             username=details['username'],
                                             key=details['key'],
                                             options=DEFAULT_SSH_OPTIONS)

    def setup(self, variables=None, **kwargs):
        variables = variables or {}

        # workaround-openstack-hostname.yaml requires hostname and domainname.
        # If not set, create ones - some tests may depend on resolvable hostname.
        if 'GUEST_HOSTNAME' not in variables:
            variables['GUEST_HOSTNAME'] = re.sub(r'10\.(\d+)\.(\d+)\.(\d+)', r'host-\1-\2-\3', str(self._ip.ip))

        if 'GUEST_DOMAINNAME' not in variables:
            variables['GUEST_DOMAINNAME'] = 'host.centralci.eng.rdu2.redhat.com'

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


class CIOpenstack(Module):
    """
    This module manages Openstack instances. It provides a shared function
    to create given number of instances.

    When the module is destroyed it disassociates all associated floating IPs,
    deletes all created snapshots and removes all instances.
    """

    name = 'openstack'
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
            'help': 'Keep instance running, do not destroy',
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
            'help': """User data to pass to OpenStack when requesting instances. If the value doesn't start
with '#cloud-config', it's considered a path and module will read the actual userdata from it."""
        }
    }
    required_options = ['auth-url', 'password', 'project-name', 'username', 'ssh-key', 'ip-pool-name']
    shared_functions = ['provision']

    # connection handler
    nova = None

    # all openstack instances
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
        raise CIError("{0} '{1}' not found, available {0}\n{2}".format(type, name, ', '.join(available)))

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

    def provision(self, count=1, name=DEFAULT_NAME, image=None, flavor=None):
        """
        Provision multiple openstack instances from the given image name. The flavor is by
        default {}. The name of the instances is created from the name parameter plus the floating
        IPv4 address.

        :param int count: number of openstack instances to create
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

        # create given number of instances
        instances = []
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

            self.verbose('creating instance with following details\n{}'.format(format_dict(details)))
            instance = OpenstackGuest(self, details)

            self._all.append(instance)
            instances.append(instance)

        self.debug('created {} instances, waiting for them to become ACTIVE'.format(count))

        for instance in instances:
            instance.wait_alive(timeout=self.option('activation-time'), tick=1)

        self.info("created {} instance(s) with flavor '{}' from image '{}'".format(count, flavor, image))

        return instances

    def destroy(self, failure=None):
        if self._all and self.option('keep'):
            self.info('keeping instances provisioned, skipping removal')
            return
        for instance in self._all:
            instance.destroy()
        self.info('successfully removed all instances')

    def execute(self):
        api_version = self.option('api-version')
        auth_url = self.option('auth-url')
        project_name = self.option('project-name')
        password = self.option('password')
        username = self.option('username')
        key_name = self.option('key-name')

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
