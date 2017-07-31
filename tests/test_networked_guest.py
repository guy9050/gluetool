import socket
import pytest

from mock import MagicMock

import libci
import libci.guest
import libci.log
import libci.utils

from . import NonLoadingCI, Bunch


@pytest.fixture(name='guest')
def fixture_guest():
    ci = NonLoadingCI()
    mod = libci.Module(ci, 'dummy-module')
    guest = libci.guest.NetworkedGuest(mod, '10.20.30.40', 'dummy-guest', port=13, username='ssh-user',
                                       key='/tmp/ssh.key', options=['Foo=17'])

    return guest


@pytest.fixture(name='sock')
def fixture_sock(monkeypatch):
    sock = MagicMock()
    sock.settimeout = MagicMock()

    monkeypatch.setattr(socket, 'socket', MagicMock(return_value=sock))

    return sock


@pytest.fixture(name='copy_guest')
def fixture_copy_guest(guest, monkeypatch):
    output = Bunch(exit_code=17)
    monkeypatch.setattr(guest, '_execute', MagicMock(return_value=output))

    return guest, output


def test_sanity(guest):
    assert guest.name == 'dummy-guest'
    assert guest.hostname == '10.20.30.40'
    assert guest.port == 13
    assert guest.username == 'ssh-user'
    assert guest.key == '/tmp/ssh.key'
    assert guest.options == ['Foo=17']
    # pylint: disable=protected-access
    assert guest._ssh == ['ssh', '-P', '13', '-l', 'ssh-user', '-i', '/tmp/ssh.key', '-o', 'Foo=17']
    assert guest._scp == ['scp', '-P', '13', '-i', '/tmp/ssh.key', '-o', 'Foo=17']


def test_sshize_options():
    assert libci.guest.sshize_options(['Foo=11', 'Bar=baz']) == ['-o', 'Foo=11', '-o', 'Bar=baz']


def test_repr(guest):
    assert repr(guest) == 'ssh-user@10.20.30.40:13'


def test_private_execute(guest, monkeypatch):
    output = MagicMock()
    monkeypatch.setattr(libci.utils, 'run_command', MagicMock(return_value=output))

    # pylint: disable=protected-access
    assert guest._execute('/usr/bin/foo', bar='baz') == output

    # pylint: disable=no-member
    libci.utils.run_command.assert_called_once_with('/usr/bin/foo', logger=guest.logger, bar='baz')


def test_execute(guest, monkeypatch):
    output = MagicMock()
    monkeypatch.setattr(guest, '_execute', MagicMock(return_value=output))

    assert guest.execute('/usr/bin/foo', bar='baz') == output

    # pylint: disable=protected-access
    guest._execute.assert_called_once_with(['ssh', '-P', '13', '-l', 'ssh-user', '-i', '/tmp/ssh.key', '-o', 'Foo=17',
                                            '10.20.30.40', '/usr/bin/foo'], bar='baz')


def test_execute_ssh_options(guest, monkeypatch):
    output = MagicMock()
    monkeypatch.setattr(guest, '_execute', MagicMock(return_value=output))

    assert guest.execute('/usr/bin/foo', ssh_options=['Bar=23'], bar='baz') == output

    # pylint: disable=protected-access
    guest._execute.assert_called_once_with(['ssh', '-P', '13', '-l', 'ssh-user', '-i', '/tmp/ssh.key', '-o', 'Foo=17',
                                            '-o', 'Bar=23', '10.20.30.40', '/usr/bin/foo'], bar='baz')


@pytest.mark.parametrize('exit_codes, expected', [
    ([0, 0], (True, False)),
    ([1, 0], (False, True)),
    ([1, 1], (False, False))
])
def test_discover_rc(guest, monkeypatch, exit_codes, expected):
    def mock_execute(*args, **kwargs):
        # pylint: disable=unused-argument
        return Bunch(exit_code=exit_codes.pop(0))

    monkeypatch.setattr(guest, 'execute', mock_execute)

    # pylint: disable=protected-access
    assert guest._supports_systemctl is None
    assert guest._supports_initctl is None

    guest._discover_rc_support()

    assert guest._supports_systemctl is expected[0]
    assert guest._supports_initctl is expected[1]


@pytest.mark.parametrize('raise_error, expected', [
    ([False, False], (True, False)),
    ([True, False], (False, True)),
    ([True, True], (False, False))
])
def test_discover_rc_error(guest, monkeypatch, raise_error, expected):
    # pylint: disable=function-redefined
    def mock_execute(*args, **kwargs):
        # pylint: disable=unused-argument
        if raise_error.pop(0) is True:
            raise libci.CICommandError(None, Bunch(exit_code=1))

        return Bunch(exit_code=0)

    monkeypatch.setattr(guest, 'execute', mock_execute)

    # pylint: disable=protected-access
    assert guest._supports_systemctl is None
    assert guest._supports_initctl is None

    guest._discover_rc_support()

    assert guest._supports_systemctl is expected[0]
    assert guest._supports_initctl is expected[1]


def test_connectivity_check(guest, sock):
    sock.connect = MagicMock()

    # pylint: disable=protected-access
    assert guest._check_connectivity() is True
    assert sock.connect.called is True


def test_connectivity_check_error(guest, sock):
    sock.connect = MagicMock(side_effect=IOError)

    # pylint: disable=protected-access
    assert guest._check_connectivity() is False


@pytest.mark.parametrize('message, raises_error, ssh_options, expected', [
    ('  guest 10.20.30.40 is alive  ', False, None, True),
    ('  guest 10.20.30.40 is alive  ', False, ['Baz=17'], True),
    (' go away...  ', False, None, False),
    (' meh ', True, None, False)
])
# pylint: disable=too-many-arguments
def test_echo_check(guest, monkeypatch, message, raises_error, ssh_options, expected):
    if raises_error:
        # CICommandError needs arguments, and lambda does not allow throwing exceptions...
        def throw(*args, **kwargs):
            # pylint: disable=unused-argument

            raise libci.CICommandError(None, Bunch(exit_code=1))

        mock_execute = MagicMock(side_effect=throw)

    else:
        mock_execute = MagicMock(return_value=Bunch(stdout=message))

    monkeypatch.setattr(guest, 'execute', mock_execute)

    # pylint: disable=protected-access
    assert guest._check_echo(ssh_options=ssh_options) is expected
    mock_execute.assert_called_once_with("echo 'guest {} is alive'".format(guest.hostname), ssh_options=ssh_options)


def _test_copy_to(guest, output, recursive=False):
    assert guest.copy_to('/foo', '/bar', recursive=recursive, dummy=19) == output

    expected_cmd = ['scp', '-P', '13', '-i', '/tmp/ssh.key', '-o', 'Foo=17', '/foo', 'ssh-user@10.20.30.40:/bar']

    if recursive is True:
        expected_cmd.insert(7, '-r')

    # pylint: disable=protected-access
    guest._execute.assert_called_once_with(expected_cmd, dummy=19)


def test_copy_to(copy_guest):
    _test_copy_to(*copy_guest)


def test_copy_to_recursive(copy_guest):
    _test_copy_to(*copy_guest, recursive=True)


def _test_copy_from(guest, output, recursive=False):
    assert guest.copy_from('/foo', '/bar', recursive=recursive, dummy=19) == output

    expected_cmd = ['scp', '-P', '13', '-i', '/tmp/ssh.key', '-o', 'Foo=17', 'ssh-user@10.20.30.40:/foo', '/bar']

    if recursive is True:
        expected_cmd.insert(7, '-r')

    # pylint: disable=protected-access
    guest._execute.assert_called_once_with(expected_cmd, dummy=19)


def test_copy_from(copy_guest):
    _test_copy_from(*copy_guest)


def test_copy_from_recursive(copy_guest):
    _test_copy_from(*copy_guest, recursive=True)
