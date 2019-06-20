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


def test_missing_required_shared(module, monkeypatch):
    assert_shared('run_playbook', module.execute)

    module._config['playbooks-map'] = 'map.yml'

    patch_shared(monkeypatch, module, {
        'run_playbook': None
    })

    assert_shared('evaluate_rules', module.execute)


def test_setup(log, module, local_guest, monkeypatch):
    playbooks = ['dummy-playbook-1.yml', 'dummy-playbook-2.yml']

    def dummy_run_playbook(_playbook, _guest, variables=None, **kwargs):
        assert log.match(message='setting up with playbooks {}'.format(', '.join([
            os.path.join(os.getcwd(), playbook) for playbook in playbooks
        ])))

        assert _guest == local_guest
        # key1:val1 is gone because extra-vars option overrides it
        assert variables == {
            'key2': 'val2',
            'key3': 'val3',
            'key4': 'val4'
        }
        assert kwargs == {
            'dummy_option': 17,
            'log_filepath': 'guest-setup-{}/guest-setup-output.txt'.format(local_guest.name)
        }

        return None

    # pylint: disable=protected-access
    module._config['playbooks'] = ','.join(playbooks)
    module._config['extra-vars'] = ['key2=val2,key3=val3', 'key4=val4']

    patch_shared(monkeypatch, module, {
        'detect_ansible_interpreter': []
    }, callables={
        'run_playbook': dummy_run_playbook
    })

    module.shared('setup_guest', local_guest, variables={'key1': 'val1'}, dummy_option=17)


def test_playbook_map_guest_setup(module, monkeypatch):
    module._config['playbooks-map'] = 'map.yml'

    patch_shared(monkeypatch, module, {
        'detect_ansible_interpreter': []
    })

    monkeypatch.setattr(module, "_get_details_from_map", lambda: ([], {}))

    module.shared('setup_guest', MagicMock())


def test_playbook_map(module, monkeypatch):
    module._config['playbooks-map'] = 'map.yml'

    rules_engine = gluetool_modules.helpers.rules_engine.RulesEngine(module.glue, 'rules-engine')

    # test default context
    patch_shared(monkeypatch, module, {
        'eval_context': {
            'BUILD_TARGET': 'rhel-7.0-candidate',
        }
    }, callables={
        'evaluate_rules': rules_engine.evaluate_rules
    })

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
