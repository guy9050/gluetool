import pytest

import libci
import libci.modules.helpers.guest_setup

from . import create_module


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.helpers.guest_setup.GuestSetup)


def test_sanity(module):
    ci, _ = module

    assert ci.has_shared('setup_guest') is True


def test_missing_ansible_support(module):
    ci, _ = module

    with pytest.raises(libci.CIError, match=r"Module requires Ansible support, did you include 'ansible' module\?"):
        ci.shared('setup_guest', [])


def test_setup(log, module):
    ci, mod = module

    playbooks = ['dummy-playbook-1.yml', 'dummy-playbook-2.yml']
    guests = ['dummy.guest.number.one', 'dummy.guest.number.two']

    def dummy_run_playbook(_playbook, _guests, **kwargs):
        expected_playbook = playbooks.pop(0)

        assert log.records[-1].message \
            == "setting the guests '{}' up with '{}'".format(', '.join(guests), expected_playbook)

        assert _playbook == expected_playbook
        assert _guests == guests
        assert kwargs == {'dummy_option': 17}

        return None

    # pylint: disable=protected-access
    mod._config['playbooks'] = ','.join(playbooks)
    ci._add_shared('run_playbook', None, dummy_run_playbook)

    ci.shared('setup_guest', guests, dummy_option=17)

    assert not playbooks