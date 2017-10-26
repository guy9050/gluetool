"""
'Guest' is a (possibly remote) system citool modules can run tests
on. It provides some basic functionality to its users, e.g. you can
copy files to it, and execute commands on it, and the rest is up to
the modules.
"""

import os
import socket
import tempfile

from functools import partial

import libci


class GuestLoggingAdapter(libci.log.ContextAdapter):
    """
    Custom logger adapter, adding guest's name as a context.
    """

    def __init__(self, logger, guest):
        super(GuestLoggingAdapter, self).__init__(logger, {'ctx_guest_name': (20, guest.name)})


class Guest(object):
    """
    Base class of "remote system that can run our tests" instances.
    """

    def __init__(self, module, name):
        self._module = module
        self.name = name

        self.logger = GuestLoggingAdapter(module.logger, self)
        self.logger.connect(self)

    def destroy(self):
        """
        Destroy guest. Free its resources, and no one should be able to use it
        after this method finishes.
        """

        raise NotImplementedError()

    def setup(self, variables=None):
        """
        Setup guest before testing. This is up to child classes to implement - it
        may be a mix of direct commands, temporary files, Ansible playbooks (via
        ``guest-setup`` module).
        """

        raise NotImplementedError()

    @property
    def supports_snapshots(self):
        """
        Returns `True` if it's possible to create and re-use snapshots of the guest.
        """

        return False

    def create_snapshot(self):
        """
        Create a snapshot of the guest.

        :returns: Generic identificator that user is expected to pass to `restore_snapshot`
          when he intents to get the snapshot restored.
        """

        raise NotImplementedError()

    def restore_snapshot(self, snapshot):
        """
        Restore given snapshot.

        :returns: a guest. It may be a completely different instance of `Guest`, but
          in any case represents the guest with requested snapshot restored.
        """

        raise NotImplementedError()

    def execute(self, cmd, **kwargs):
        """
        Execute a command on the guest. Should behave like `utils.run_command`.
        """

        raise NotImplementedError()

    def copy_to(self, src, dst, recursive=False, **kwargs):
        """
        Copy a file (or a tree) from local filesystem to the guest.
        """

        raise NotImplementedError()

    def copy_from(self, src, dst, recursive=False, **kwargs):
        """
        Copy a file (or a tree) from the guest to local filesystem.
        """

        raise NotImplementedError()

    def wait(self, label, check, timeout=None, tick=30):
        """
        Wait for the guest to become responsive (e.g. after reboot).

        :param str label: printable label used for logging.
        :param callable check: called to test the condition. If its return value evaluates as ``True``,
            the condition is assumed to pass the test and waiting ends.
        :param int timeout: fail after this many seconds. ``None`` means test forever.
        :param int tick: test condition every ``tick`` seconds.
        :raises CIError: when ``timeout`` elapses while condition did not pass the check.
        """

        return libci.utils.wait(label, check, timeout=timeout, tick=tick, logger=self.logger)

    def create_file(self, dst, content):
        """
        Given the name and content, create a file on the guest.
        """

        with tempfile.NamedTemporaryFile() as f:
            f.write(content)
            f.flush()

            self.copy_to(f.name, dst)

    def create_repo(self, name, label, baseurl, **kwargs):
        """
        Given name and its properties, create a repository config file
        on the guest.
        """

        repo = """[{}]
name={}
baseurl={}
{}
""".format(name, label, baseurl, '\n'.join(['{}={}'.format(k, v) for k, v in kwargs.iteritems()]))

        self.create_file(os.path.join(os.sep, 'etc', 'yum.repos.d', '{}.repo'.format(name)), repo)


def sshize_options(options):
    return sum([['-o', option] for option in options], [])


class NetworkedGuest(Guest):
    # pylint reports some abstract methods are not implemented by this method.
    # That is expected, methods create_snapshot, restore_snapshot, destroy
    # are left for NetworkedGuest children.
    # pylint: disable=abstract-method

    """
    Guest, accessible over network, using ssh for control.

    :param libci.Module module: parent module
    :param str hostname: box hostname - this is used for connecting to the host.
    :param str name: box name - this one appears in log messages, identifies the guest.
      If not set, `hostname` is used.
    :param int port: SSH port (default: 22).
    :param str username: SSH username (default: root).
    :param str key: path to a key file.
    :param list(str) options: list of 'key=value' strings, passed as '-o' options to ssh.
    """

    DEFAULT_SSH_PORT = 22

    #: List of services that are allowed to be degraded when boot process finishes.
    ALLOW_DEGRADED = []

    # pylint: disable=too-many-arguments
    def __init__(self, module, hostname, name=None, port=None, username=None, key=None, options=None):
        name = name or hostname
        super(NetworkedGuest, self).__init__(module, name)

        self.hostname = hostname
        self.port = int(port) if port is not None else self.DEFAULT_SSH_PORT
        self.username = username
        self.key = key
        self.options = options or []

        self._ssh = ['ssh']
        self._scp = ['scp']

        if port:
            self._ssh += ['-P', str(port)]
            self._scp += ['-P', str(port)]

        if username:
            self._ssh += ['-l', username]

        if key:
            self._ssh += ['-i', key]
            self._scp += ['-i', key]

        options = sshize_options(self.options)

        self._ssh += options
        self._scp += options

        self._supports_systemctl = None
        self._supports_initctl = None

    def __repr__(self):
        return '{}{}:{}'.format((self.username + '@') if self.username is not None else '', self.hostname, self.port)

    def setup(self, **kwargs):
        # pylint: disable=arguments-differ
        if not self._module.has_shared('setup_guest'):
            raise libci.CIError("Module 'guest-setup' is required to actually set the guests up.")

        return self._module.shared('setup_guest', [self.hostname], **kwargs)

    def _execute(self, cmd, **kwargs):
        return libci.utils.run_command(cmd, logger=self.logger, **kwargs)

    def execute(self, cmd, ssh_options=None, **kwargs):
        # pylint: disable=arguments-differ

        ssh_options = ssh_options or []

        return self._execute(self._ssh + sshize_options(ssh_options) + [self.hostname] + [cmd], **kwargs)

    def _discover_rc_support(self):
        self._supports_systemctl = False
        self._supports_initctl = False

        try:
            output = self.execute('type systemctl')
        except libci.CICommandError as exc:
            output = exc.output

        if output.exit_code == 0:
            self._supports_systemctl = True
            return

        try:
            output = self.execute('type initctl')
        except libci.CICommandError as exc:
            output = exc.output

        if output.exit_code == 0:
            self._supports_initctl = True
            return

    def _check_connectivity(self):
        """
        Check whether guest is reachable over network by inspecting its ssh port.
        """

        addrinfo = socket.getaddrinfo(self.hostname, self.port, 0, socket.SOCK_STREAM)
        (family, socktype, proto, _, sockaddr) = addrinfo[0]

        sock = socket.socket(family, socktype, proto)
        sock.settimeout(1)

        # pylint: disable=bare-except
        try:
            sock.connect(sockaddr)
            return True

        except:
            pass

        finally:
            sock.close()

        return False

    def _check_echo(self, ssh_options=None):
        """
        Check whether remote shell is available by running simple ``echo`` command.
        """

        msg = 'guest {} is alive'.format(self.hostname)

        try:
            output = self.execute("echo '{}'".format(msg), ssh_options=ssh_options)

            if output.stdout.strip() == msg:
                return True

        except libci.CICommandError:
            self.debug('echo attempt failed, ignoring error')

        return False

    def _get_rc_status(self, cmd, ssh_options=None):
        try:
            output = self.execute(cmd, ssh_options=ssh_options)

        except libci.CICommandError as exc:
            output = exc.output

        return output.stdout.strip()

    def _check_boot_systemctl(self, ssh_options=None):
        """
        Check whether boot process finished using ``systemctl``.
        """

        status = self._get_rc_status('systemctl is-system-running', ssh_options=ssh_options)

        if status == 'running':
            self.debug('systemctl reports ready')
            return True

        if status == 'degraded':
            output = self.execute('systemctl --plain --no-pager --failed', ssh_options=ssh_options)
            report = output.stdout.strip().split('\n')

            degraded_services = [line.strip() for line in report if line.startswith(' ')]

            def _allowed_degraded(line):
                return any((line.startswith(service) for service in self.ALLOW_DEGRADED))

            filtered = [line for line in degraded_services if not _allowed_degraded(line)]

            if not filtered:
                self.debug('only ignored services are degraded, report ready')
                return True

            raise libci.CIError('unexpected services reported as degraded')

        self.debug("systemctl not reporting ready: '{}'".format(status))
        return False

    def _check_boot_initctl(self, ssh_options=None):
        """
        Check whether boot process finished using ``initctl``.
        """

        status = self._get_rc_status('initctl status rc', ssh_options=ssh_options)

        if status == 'rc stop/waiting':
            self.debug('initctl reports ready')
            return True

        return False

    def wait_alive(self, connect_timeout=None, connect_tick=10,
                   echo_timeout=None, echo_tick=30,
                   boot_timeout=None, boot_tick=10):
        self.debug('waiting for guest to become alive')

        # Step #1: check connectivity first - let's see whether ssh port is connectable
        self.wait('connectivity', self._check_connectivity,
                  timeout=connect_timeout, tick=connect_tick)

        # Step #2: connect to ssh and see whether shell works by printing something
        self.wait('shell available', partial(self._check_echo, ssh_options=['ConnectTimeout={:d}'.format(echo_tick)]),
                  timeout=echo_timeout, tick=echo_tick)

        # Step #3: check system services, there are ways to tell system boot process finished
        if self._supports_systemctl is None or self._supports_initctl is None:
            self._discover_rc_support()

        if self._supports_systemctl is True:
            check_boot = self._check_boot_systemctl

        elif self._supports_initctl is True:
            check_boot = self._check_boot_initctl

        else:
            self.warn("Don't know how to check boot process status - assume it finished and hope for the best")
            return

        check_boot = partial(check_boot, ssh_options=['ConnectTimeout={:d}'.format(boot_tick)])

        self.wait('boot finished', check_boot, timeout=boot_timeout, tick=boot_tick)

    def copy_to(self, src, dst, recursive=False, **kwargs):
        self.debug("copy to the guest: '{}' => '{}'".format(src, dst))

        cmd = self._scp[:]

        if recursive:
            cmd += ['-r']

        cmd += [src, '{}@{}:{}'.format(self.username, self.hostname, dst)]

        return self._execute(cmd, **kwargs)

    def copy_from(self, src, dst, recursive=False, **kwargs):
        self.debug("copy from the guest: '{}' => '{}'".format(src, dst))

        cmd = self._scp[:]

        if recursive:
            cmd += ['-r']

        cmd += ['{}@{}:{}'.format(self.username, self.hostname, src), dst]

        return self._execute(cmd, **kwargs)
