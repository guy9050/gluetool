import collections
import gluetool

from gluetool import GlueError, SoftGlueError
from gluetool.log import log_dict
from gluetool.result import Result
from gluetool.utils import treat_url, normalize_multistring_option
from libci.guest import NetworkedGuest

from gluetool_modules.libs.testing_environment import TestingEnvironment

from typing import Any, Dict, List, Optional, Tuple, cast  # noqa

DEFAULT_PRIORIY_GROUP = 'default-priority'
DEFAULT_READY_TIMEOUT = 300
DEFAULT_READY_TICK = 3
DEFAULT_ACTIVATION_TIMEOUT = 240
DEFAULT_ACTIVATION_TICK = 5
DEFAULT_ECHO_TIMEOUT = 240
DEFAULT_ECHO_TICK = 10
DEFAULT_BOOT_TIMEOUT = 240
DEFAULT_BOOT_TICK = 10
DEFAULT_SSH_OPTIONS = ['UserKnownHostsFile=/dev/null', 'StrictHostKeyChecking=no']

#: Artemis provisioner capabilities.
#: Follows :doc:`Provisioner Capabilities Protocol </protocols/provisioner-capabilities>`.
ProvisionerCapabilities = collections.namedtuple('ProvisionerCapabilities', ['available_arches'])


class ArtemisAPIError(SoftGlueError):
    def __init__(self, response, error=None):
        # type: (Any, Optional[str]) -> None

        self.status_code = response.status_code
        self.json = {}  # type: Dict[str, str]
        self.text = response.text.encode('ascii', 'replace')  # type: str
        self._errors = error

        # We will look at response's headers to try to guess if response's content is json serializable
        # If yes, we will expect it to either have 'message' or 'errors' key, it's value could be used in exception
        # If no, we will use raw text in exception instead
        headers = {key.lower(): response.headers[key] for key in response.headers}

        if headers.get('content-type') and 'application/json' in headers['content-type']:
            try:
                self.json = response.json()
            except Exception as exc:
                self.json['errors'] = str(exc)

        super(ArtemisAPIError, self).__init__(
            'Call to Artemis API failed, HTTP {}: {}'.format(
                self.status_code, self.errors))

    @property
    def errors(self):
        # type: () -> str

        if self._errors:
            return self._errors

        if self.json.get('message'):
            return self.json['message']

        if self.json.get('errors'):
            return self.json['errors']

        return self.text


class ArtemisAPI(object):
    ''' Class that allows RESTful communication with Artemis API '''

    def __init__(self, module, api_url):
        # type: (gluetool.Module, str) -> None

        self.module = module
        self.url = treat_url(api_url)
        self.check_if_artemis()

    def check_if_artemis(self):
        # type: () -> None
        '''
        Checks if `url` actually points to ArtemisAPI by calling '/guests' endpoint (which should always return a list)
        '''

        def error(response):
            # type: (Any) -> ArtemisAPIError
            err_msg = 'URL {} does not point to Artemis API. Expected list, got {}' \
                .format(self.url, response.text.encode('ascii', 'replace'))
            err = ArtemisAPIError(response, error=err_msg)
            return err

        with gluetool.utils.requests() as request:
            response = request.get('{}guests/'.format(self.url))

        try:
            if response.status_code != 200 or not isinstance(response.json(), list):
                raise Exception
        except Exception:
            raise error(response)

    def create_guest(self, environment, keyname=None, priority=None, compose_type=None):
        # type: (TestingEnvironment, Optional[str], Optional[str], Optional[str]) -> Any
        '''
        Submits a guest request to Artemis API.

        :param tuple environment: description of the environment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.

        :param str keyname: name of key stored in Artemis configuration.

        :param str priority: Priority group of the guest request.
            See Artemis API docs for more.

        :param str compose_type: Desired guest request compose type (openstack, beaker, AWS, etc). If it's None, Artemis
            will try to choose appropriate compose type by itself.
            See Artemis API docs for more.

        :rtype: dict
        :returns: Artemis API response serialized as dictionary or ``None`` in case of failure.
        '''

        compose = environment.compose

        data = {
            'keyname': keyname,
            'environment': {
                'arch': environment.arch,
                'compose': {}
            },
            'priority_group': priority
        }  # type: Dict[str, Any]

        if not compose_type or compose_type == 'id':
            # probably this was called within provision(), called as a shared function from pipeline,
            # not from artemis' execute method, which means we will let Artemis decide which compose type
            # is going to be used
            data['environment']['compose'] = {'id': compose}
        elif compose_type == 'beaker':
            data['environment']['compose'] = {
                'beaker': {
                    'distro': compose
                }
            }
        elif compose_type == 'openstack':
            data['environment']['compose'] = {
                'openstack': {
                    'image': compose
                }
            }
        elif compose_type == 'aws':
            data['environment']['compose'] = {
                'aws': {
                    'image': compose
                }
            }

        with gluetool.utils.requests() as request:
            response = request.post('{}guests/'.format(self.url), json=data)

        if response.status_code == 201:
            return response.json()

        raise ArtemisAPIError(response)

    def inspect_guest(self, guest_id):
        # type: (str) -> Any
        '''
        Requests Artemis API for data abput a specific guest.

        :param str guest_id: Artemis guestname (or guest id).
            See Artemis API docs for more.

        :rtype: dict
        :returns: Artemis API response serialized as dictionary or ``None`` in case of failure.
        '''

        with gluetool.utils.requests() as request:
            response = request.get('{}guests/{}'.format(self.url, guest_id))

        if response.status_code == 200:
            return response.json()

        raise ArtemisAPIError(response)

    def cancel_guest(self, guest_id):
        # type: (str) -> Any
        '''
        Requests Artemis API to cancel guest provision (or, in case a guest os already provisioned, return the guest).

        :param str guest_id: Artemis guestname (or guest id).
            See Artemis API docs for more.

        :rtype: dict
        :returns: Artemis API response serialized as dictionary or ``None`` in case of failure.
        '''

        with gluetool.utils.requests() as request:
            response = request.delete('{}guests/{}'.format(self.url, guest_id))

        if response.status_code == 200:
            return response.json()

        raise ArtemisAPIError(response)


class ArtemisGuest(NetworkedGuest):

    def __init__(self,
                 module,  # type: ArtemisProvisioner
                 guestname,  # type: str
                 hostname,  # type: str
                 environment,  # type: TestingEnvironment
                 port=None,  # type: Optional[int]
                 username=None,  # type: Optional[str]
                 key=None,  # type: Optional[str]
                 options=None,  # type: Optional[List[str]]
                 **kwargs   # type: Optional[Dict[str, Any]]
                 ):

        super(ArtemisGuest, self).__init__(module,
                                           hostname,
                                           environment=environment,
                                           name=guestname,
                                           port=port,
                                           username=username,
                                           key=key,
                                           options=options)
        self.artemis_id = guestname

    def __str__(self):
        # type: () -> str
        return 'ArtemisGuest({}, {})'.format(self.artemis_id, self.environment)

    def _check_ip_ready(self):
        # type: () -> Result[bool, str]

        # Initialize these variables - should the exception happen inside inspect_guest, the Error()
        # would fail because these would be left undefined due to early quit from try branch.
        guest_state = None
        guest_address = None

        try:
            guest_data = cast(ArtemisProvisioner, self._module).api.inspect_guest(self.artemis_id)
            guest_state = guest_data['state']
            guest_address = guest_data['address']
            if guest_state == 'ready':
                if guest_address:
                    return Result.Ok(True)

        except Exception as e:
            self.warn('Exception raised: {}'.format(e))

        return Result.Error("Couldn't get address for guest {} (state={}, address={})".format(self.artemis_id,
                                                                                              guest_state,
                                                                                              guest_address))

    def _wait_ready(self, timeout, tick):
        # type: (int, int)-> None
        '''
        Wait till the guest is ready to be provisined, which it's IP/hostname is available
        '''

        try:
            self.wait('ip_ready', self._check_ip_ready, timeout=timeout, tick=tick)

        except GlueError as exc:
            raise GlueError("Guest couldn't be provisioned: {}".format(exc))

    def _wait_alive(self, connect_timeout, connect_tick, echo_timeout, echo_tick, boot_timeout, boot_tick):
        # type: (int, int, int, int, int, int) -> None
        '''
        Wait till the guest is alive. That covers several checks.
        '''

        try:
            self.wait_alive(connect_timeout=connect_timeout, connect_tick=connect_tick,
                            echo_timeout=echo_timeout, echo_tick=echo_tick,
                            boot_timeout=boot_timeout, boot_tick=boot_tick)

        except GlueError as exc:
            raise GlueError('Guest failed to become alive: {}'.format(exc))


class ArtemisProvisioner(gluetool.Module):
    ''' Provisions guest via Artemis API '''
    name = 'artemis'
    description = 'Provisions guest via Artemis API'
    options = [
        ('API options', {
            'api-url': {
                'help': 'Artemis API url',
                'metavar': 'URL',
                'type': str
            },
            'key': {
                'help': 'Desired guest key name',
                'metavar': 'KEYNAME',
                'type': str
            },
            'arch': {
                'help': 'Desired guest architecture',
                'metavar': 'ARCH',
                'type': str
            },
            'priority-group': {
                'help': 'Desired guest priority group (default: %(default)s)',
                'metavar': 'PRIORITY_GROUP',
                'type': str,
                'default': DEFAULT_PRIORIY_GROUP
            }
        }),
        ('Common options', {
            'keep': {
                'help': '''Keep instance(s) running, do not destroy. No reservation records are created and it is
                           expected from the user to cleanup the instance(s).''',
                'action': 'store_true'
            },
            'provision': {
                'help': 'Provision given number of guests',
                'metavar': 'COUNT',
                'type': int
            }
        }),
        ('Guest options', {
            'ssh-options': {
                'help': 'SSH options (default: none).',
                'action': 'append',
                'default': []
            },
            'ssh-key': {
                'help': 'SSH key that is used to connect to the machine',
                'type': str
            }
        }),
        ('Provisioning options', {
            'compose-id': {
                'help': 'Desired guest compose',
                'metavar': 'ID',
                'type': str
            },
            'distro': {
                'help': 'Desired Beaker guest distro',
                'metavar': 'DISTRO',
                'type': str
            },
            'openstack-image': {
                'help': 'Desired Openstack image',
                'metavar': 'IMAGE',
                'type': str
            },
            'aws-image': {
                'help': 'Desired AWS image',
                'metavar': 'IMAGE',
                'type': str
            }
        }),
        ('Timeout options', {
            'ready-timeout': {
                'help': 'Timeout for guest to become ready (default: %(default)s)',
                'metavar': 'READY_TIMEOUT',
                'type': int,
                'default': DEFAULT_READY_TIMEOUT
            },
            'ready-tick': {
                'help': 'Check every READY_TICK seconds if a guest has become ready (default: %(default)s)',
                'metavar': 'READY_TICK',
                'type': int,
                'default': DEFAULT_READY_TICK
            },
            'activation-timeout': {
                'help': 'Timeout for guest to become active (default: %(default)s)',
                'metavar': 'ACTIVATION_TIMEOUT',
                'type': int,
                'default': DEFAULT_ACTIVATION_TIMEOUT
            },
            'activation-tick': {
                'help': 'Check every ACTIVATION_TICK seconds if a guest has become active (default: %(default)s)',
                'metavar': 'ACTIVATION_TICK',
                'type': int,
                'default': DEFAULT_ACTIVATION_TICK
            },
            'echo-timeout': {
                'help': 'Timeout for guest echo (default: %(default)s)',
                'metavar': 'ECHO_TIMEOUT',
                'type': int,
                'default': DEFAULT_ECHO_TIMEOUT
            },
            'echo-tick': {
                'help': 'Echo guest every ECHO_TICK seconds (default: %(default)s)',
                'metavar': 'ECHO_TICK',
                'type': int,
                'default': DEFAULT_ECHO_TICK
            },
            'boot-timeout': {
                'help': 'Timeout for guest boot (default: %(default)s)',
                'metavar': 'BOOT_TIMEOUT',
                'type': int,
                'default': DEFAULT_BOOT_TIMEOUT
            },
            'boot-tick': {
                'help': 'Check every BOOT_TICK seconds if a guest has boot (default: %(default)s)',
                'metavar': 'BOOT_TICK',
                'type': int,
                'default': DEFAULT_BOOT_TICK
            }
        })
    ]

    required_options = ('api-url', 'key', 'priority-group', 'ssh-key',)

    shared_functions = ['provision', 'provisioner_capabilities']

    def sanity(self):
        # type: () -> None

        if not self.option('provision'):
            return

        if not self.option('arch'):
            raise GlueError('Missing required option: --arch')

        # provisioning options - it is required to have one of them
        provisioning_opts = cast(Dict[str, Any], self.options[3][1]).keys()

        if not any([self.option(option) for option in provisioning_opts]):
            raise GlueError('At least one of those options is required: {}'.format(', '.join(provisioning_opts)))

    def __init__(self, *args, **kwargs):
        # type: (Any, Any) -> None
        super(ArtemisProvisioner, self).__init__(*args, **kwargs)

        self.guests = []  # type: List[ArtemisGuest]
        self.api = None  # type: ArtemisAPI  # type: ignore

    def provisioner_capabilities(self):
        # type: () -> ProvisionerCapabilities
        '''
        Return description of Artemis provisioner capabilities.

        Follows :doc:`Provisioner Capabilities Protocol </protocols/provisioner-capabilities>`.
        '''

        return ProvisionerCapabilities(
            available_arches=['x86_64']
        )

    def provision_guest(self,
                        environment,  # type: TestingEnvironment
                        key=None,  # type: Optional[str]
                        priority=None,  # type: Optional[str]
                        compose_type=None,  # type: Optional[str]
                        ssh_key=None,  # type: Optional[str]
                        options=None  # type: Optional[List[str]]
                       ):  # noqa
        # type: (...) -> ArtemisGuest
        '''
        Provision Artemis guest by submitting a request to Artemis API.

        :param tuple environment: description of the environment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.

        :param str key: name of key stored in Artemis configuration.

        :param str priority: Priority group of the guest request.
            See Artemis API docs for more.

        :param str compose_type: Desired guest request compose type (openstack, beaker, AWS, etc). If it's None, Artemis
            will try to choose appropriate compose type by itself.
            See Artemis API docs for more.

        :param str ssh_key: the path to public key, that should be used to securely connect to a provisioned machine.
            See Artemis API docs for more.

        :param list option: SSH options that would be used when securely connecting to a provisioned guest via SSH.

        :rtype: ArtemisGuest
        :returns: ArtemisGuest instance or ``None`` if it wasn't possible to grab the guest.
        '''

        response = self.api.create_guest(environment,
                                         keyname=key,
                                         priority=priority,
                                         compose_type=compose_type)

        guest = ArtemisGuest(self, response['guestname'], response['address'], environment,
                             port=response['ssh']['port'], username=response['ssh']['username'],
                             key=ssh_key, options=options)

        guest.info('Guest is being provisioned')
        log_dict(guest.debug, 'Created guest request', response)

        guest._wait_ready(timeout=self.option('ready-timeout'), tick=self.option('ready-tick'))
        response = self.api.inspect_guest(guest.artemis_id)
        guest.hostname = response['address']
        guest.info('Guest is ready')

        guest._wait_alive(self.option('activation-timeout'), self.option('activation-tick'),
                          self.option('echo-timeout'), self.option('echo-tick'),
                          self.option('boot-timeout'), self.option('boot-tick'))
        guest.info('Guest has become alive')

        return guest

    def provision(self, environment, provision_count=1, **kwargs):
        # type: (TestingEnvironment, int, Any) -> List[ArtemisGuest]
        '''
        Provision Artemis guest(s).

        :param tuple environment: description of the environment caller wants to provision.
            Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.

        :rtype: list
        :returns: List of ArtemisGuest instances or ``None`` if it wasn't possible to grab the guests.
        '''

        key = self.option('key')
        ssh_key = self.option('ssh-key')
        priority = self.option('priority-group')
        options = normalize_multistring_option(self.option('ssh-options'))
        compose_type = kwargs.pop('compose_type', None)

        for _ in range(provision_count):
            guest = self.provision_guest(environment,
                                         key=key,
                                         priority=priority,
                                         compose_type=compose_type,
                                         ssh_key=ssh_key,
                                         options=options)
            guest.info('Guest provisioned')
            self.guests.append(guest)

        return self.guests

    def execute(self):
        # type: () -> None

        self.api = ArtemisAPI(self, self.option('api-url'))
        # TODO: print Artemis API version when version endpoint is implemented
        self.info('Using Artemis API {}'.format(self.api.url))

        if not self.option('provision'):
            return

        provision_count = self.option('provision')
        arch = self.option('arch')
        compose_type = None
        compose = None

        if self.option('openstack-image'):
            compose_type = 'openstack'
            compose = self.option('openstack-image')
        elif self.option('distro'):
            compose_type = 'beaker'
            compose = self.option('distro')
        elif self.option('compose-id'):
            compose_type = 'id'
            compose = self.option('compose-id')
        elif self.option('aws-image'):
            compose_type = 'aws'
            compose = self.option('aws-image')

        environment = TestingEnvironment(arch=arch,
                                         compose=compose)

        self.provision(environment,
                       provision_count=provision_count,
                       compose_type=compose_type)

    def destroy(self, failure=None):
        # type: (Optional[Any]) -> None
        if self.option('keep'):
            return

        for guest in self.guests:
            guest.info('Canceling guest')
            self.api.cancel_guest(guest.artemis_id)
            guest.info('Successfully removed guest')
