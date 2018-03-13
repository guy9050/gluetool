import pytest

from mock import MagicMock

import libci.guest
import gluetool_modules.helpers.guest_setup

from . import create_module, assert_shared


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(gluetool_modules.helpers.guest_setup.GuestSetup)[1]


@pytest.fixture(name='local_guest')
def fixture_local_guest(module):
    return libci.guest.NetworkedGuest(module, '127.0.0.1', key=MagicMock())


def test_sanity(module):
    assert module.glue.has_shared('setup_guest') is True


def test_missing_ansible_support(module):
    assert_shared('run_playbook', module.setup_guest, [])


def test_setup(log, module, local_guest):
    playbooks = ['dummy-playbook-1.yml', 'dummy-playbook-2.yml']
    guests = [local_guest, local_guest]

    def dummy_run_playbook(_playbook, _guests, **kwargs):
        expected_playbook = playbooks.pop(0)

        guest_hostnames = ', '.join([guest.hostname for guest in guests])
        assert log.records[-1].message \
            == "setting the guests '{}' up with '{}'".format(guest_hostnames, expected_playbook)

        assert _playbook == expected_playbook
        assert _guests == guests
        assert kwargs == {'dummy_option': 17}

        return None

    # pylint: disable=protected-access
    module._config['playbooks'] = ','.join(playbooks)
    module.glue._add_shared('run_playbook', module, dummy_run_playbook)

    module.shared('setup_guest', guests, dummy_option=17)

    assert not playbooks
