import os
import pytest

from mock import MagicMock

import gluetool
import libci.guest
import gluetool_modules.helpers.guest_setup
import gluetool_modules.helpers.rules_engine

from . import assert_shared, create_module, patch_shared


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(gluetool_modules.helpers.guest_setup.GuestSetup)[1]


@pytest.fixture(name='local_guest')
def fixture_local_guest(module):
    return libci.guest.NetworkedGuest(module, '127.0.0.1', key=MagicMock())


def test_sanity_shared(module):
    assert module.glue.has_shared('setup_guest') is True


def test_sanity_no_required_options(module):
    with pytest.raises(gluetool.GlueError, match=r"^One of the options 'playbooks' or 'playbooks-map' is required"):
        module.sanity()


def test_sanity_both_options(module):
    module._config['playbooks'] = ['dummy1.yml', 'dummy2.yml']
    module._config['playbooks-map'] = 'map.yml'

    module.sanity()


def test_playbook_map_empty(module):
    assert module.playbooks_map == []


def test_missing_required_shared(module):
    assert_shared('run_playbook', module.execute)

    module._config['playbooks-map'] = 'map.yml'
    module.glue._add_shared('run_playbook', module, lambda: None)
    assert_shared('evaluate_rules', module.execute)


def test_setup(log, module, local_guest):
    playbooks = ['dummy-playbook-1.yml', 'dummy-playbook-2.yml']
    guests = [local_guest, local_guest]

    def dummy_run_playbook(_playbook, _guests, variables=None, **kwargs):
        expected_playbook = os.path.join(os.getcwd(), playbooks.pop(0))

        guest_hostnames = ', '.join([guest.hostname for guest in guests])
        assert log.records[-1].message \
            == "setting the guests '{}' up with '{}'".format(guest_hostnames, expected_playbook)

        assert _playbook == expected_playbook
        assert _guests == guests
        # key1:val1 is gone because extra-vars option overrides it
        assert variables == {
            'key2': 'val2',
            'key3': 'val3',
            'key4': 'val4'
        }
        assert kwargs == {'dummy_option': 17}

        return None

    # pylint: disable=protected-access
    module._config['playbooks'] = ','.join(playbooks)
    module._config['extra-vars'] = ['key2=val2,key3=val3', 'key4=val4']
    module.glue._add_shared('detect_ansible_interpreter', module, lambda guest: [])
    module.glue._add_shared('run_playbook', module, dummy_run_playbook)

    module.shared('setup_guest', guests, variables={'key1': 'val1'}, dummy_option=17)

    assert not playbooks


def test_playbook_map_guest_setup(module, monkeypatch):
    module._config['playbooks-map'] = 'map.yml'

    module.glue._add_shared('detect_ansible_interpreter', module, lambda guest: [])
    monkeypatch.setattr(module, "_get_details_from_map", lambda: ([], {}))

    module.shared('setup_guest', [MagicMock()])


def test_playbook_map(module, monkeypatch):
    module._config['playbooks-map'] = 'map.yml'

    # test default context
    patch_shared(monkeypatch, module, {
        'eval_context': {
            'BUILD_TARGET': 'rhel-7.0-candidate',
        }
    })

    rules_engine = gluetool_modules.helpers.rules_engine.RulesEngine(module.glue, 'rules-engine')
    module.glue.shared_functions['evaluate_rules'] = (rules_engine, rules_engine.evaluate_rules)

    def load_yaml(path, logger):
        return [
            {
                "playbooks": [
                    "other.yaml"
                ],
                "rule": "BUILD_TARGET.match('rhel-6')"
            },
            {
                "playbooks": [
                    "default.yaml"
                ],
                "extra_vars": {
                    "key": "value"
                },
                "rule": "BUILD_TARGET.match('rhel-7.0-candidate')"
            },
        ]

    monkeypatch.setattr(gluetool.utils, "load_yaml", load_yaml)

    assert module._get_details_from_map() == ([os.path.join(os.getcwd(), 'default.yaml')], {'key': 'value'})
