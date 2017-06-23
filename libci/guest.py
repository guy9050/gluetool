"""
'Guest' is a (possibly remote) system citool modules can run tests
on. It provides some basic functionality to its users, e.g. you can
copy files to it, and execute commands on it, and the rest is up to
the modules.
"""

import os
import socket
import tempfile
import time

import libci


class GuestLoggingAdapter(libci.log.ContextAdapter):
    """
    Custom logger adapter, adding guest's name as a context.
    """

    def __init__(self, logger, guest):
        super(GuestLoggingAdapter, self).__init__(logger, {'ctx_guest_name': (20, guest.name)})


class SnapshotContext(object):
    # pylint: disable=too-few-public-methods

    """
    Work with a guest, restoring its state using the pre-existing snapshot.

    :param Guest guest: guest we'd like to use.
    :param snapshot: Snapshot we want to restore. The actual value depends
      on the guest's implementation, and it's value and type is of no
      concern to the caller.
    """

    def __init__(self, guest, snapshot):
        self._guest = guest
        self._snapshot = snapshot

    def __enter__(self):
        """
        Restore the snapshot.

        :returns: a guest. E.g. in Openstack, "restoring a snapshot" means
          user get a different server instance, running the requested
          snapshot image, while the original box is still available to him.
          This means guest's `restore_snapshot` may return a completely
          different instance of `Guest`, other than the guest whose method
          you called. When entering this context, user gets a guest, running
          requested snapshot, and user should not expect this instance to
          be identical with the one he passed to the constructor of this context.
        """

        self._guest.debug("Restoring snapshot '{}'".format(str(self._snapshot)))
        self._snapshot = self._guest.restore_snapshot(self._snapshot)

        return self._snapshot

    def __exit__(self, *args, **kwargs):
        self._snapshot.destroy()


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

    def wait(self, check, timeout=None, tick=30):
        """
        Wait for the guest to become responsive (e.g. after reboot).

        :param check: callable performing the actual test. If its return
          value evaluates to `True`, the waiting finishes successfully.
        :param int timeout: if set, wait at max TIMEOUT seconds. If `None`,
          wait indefinitely.
        :param int tick: check guest status every TICK seconds.
        """

        assert isinstance(tick, int) and tick > 0

        if timeout is not None:
            end_time = time.time() + timeout

        def _timeout():
            return '{} seconds'.format(int(end_time - time.time())) if timeout is not None else 'infinite'

        self.debug("waiting for check '{}', {} timeout, check every {} seconds".format(check.func_name,
                                                                                       _timeout(), tick))

        while timeout is None or time.time() < end_time:
            self.debug('{} left, sleeping for {} seconds'.format(_timeout(), tick))
            time.sleep(tick)

            try:
                ret = check()
                if ret:
                    self.debug('check passed, assuming success')
                    return ret

            except libci.CICommandError:
                pass

            self.debug('check failed, assuming failure')

        raise libci.CIError('Check did not manage to pass for guest')

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

        options = sum([['-o', option] for option in self.options], [])

        self._ssh += options
        self._scp += options

    def __repr__(self):
        return '{}{}:{}'.format((self.username + '@') if self.username is not None else '', self.hostname, self.port)

    def setup(self, **kwargs):
        # pylint: disable=arguments-differ
        if not self._module.has_shared('setup_guest'):
            raise libci.CIError("Module 'guest-setup' is required to actually set the guests up.")

        return self._module.shared('setup_guest', [self.hostname], **kwargs)

    def _execute(self, cmd, **kwargs):
        return libci.utils.run_command(cmd, logger=self.logger, **kwargs)

    def execute(self, cmd, **kwargs):
        return self._execute(self._ssh + [self.hostname] + [cmd], **kwargs)

    def wait_alive(self, echo_timeout=None, echo_tick=30, **kwargs):
        self.debug('waiting for guest to become alive')

        addrinfo = socket.getaddrinfo(self.hostname, self.port, 0, socket.SOCK_STREAM)
        (family, socktype, proto, _, sockaddr) = addrinfo[0]

        def check_ssh():
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

        self.wait(check_ssh, **kwargs)

        msg = 'guest {} is alive'.format(self.hostname)

        def check_echo():
            try:
                output = self.execute("echo '{}'".format(msg))

                if output.stdout.strip() == msg:
                    return True

            except libci.CICommandError:
                self.debug('echo attempt failed, ignoring error')

        self.wait(check_echo, timeout=echo_timeout, tick=echo_tick)

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
