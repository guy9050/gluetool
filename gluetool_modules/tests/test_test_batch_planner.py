import os
import sys

import pytest

import gluetool
import gluetool_modules.helpers.rules_engine
import gluetool_modules.dispatchers.test_batch_planner

from . import create_module, patch_shared


def _load_from_assets(starts_with):
    assets = []

    assets_dir = os.path.join('gluetool_modules', 'tests', 'assets', 'test_batch_planner')

    for filename in sorted(os.listdir(assets_dir)):
        if not filename.startswith(starts_with):
            continue

        with open(os.path.join(assets_dir, filename), 'r') as f:
            assets.append(gluetool.utils.YAML.load(f))

    return assets


@pytest.fixture(name='module')
def fixture_module(monkeypatch):
    # pylint: disable=unused-argument

    module = create_module(gluetool_modules.dispatchers.test_batch_planner.TestBatchPlanner)[1]

    patch_shared(monkeypatch, module, {
        'artifact_context': {
            'BUILD_TARGET': 'dummy-target',
            'PRIMARY_TASK': 'dummy-primary-task',
            'TASKS': 'dummy-tasks',
            'NVR': 'foo-13.17-23.el7',
            'SCRATCH': False
            }
    })

    rules_engine = gluetool_modules.helpers.rules_engine.RulesEngine(module.glue, 'rules-engine')
    module.glue.shared_functions['evaluate_rules'] = (rules_engine, rules_engine.evaluate_rules)

    return module


def test_sanity(module):
    # first time `module` fixture is used

    assert isinstance(module, gluetool_modules.dispatchers.test_batch_planner.TestBatchPlanner)


def test_loadable(module):
    # pylint: disable=protected-access
    python_mod = module.glue._load_python_module('dispatchers/test-batch-planner', 'pytest_test_batch_planner',
                                                 'gluetool_modules/dispatchers/test_batch_planner.py')

    assert hasattr(python_mod, 'TestBatchPlanner')


def test_shared(module):
    module.add_shared()

    assert module.glue.has_shared('plan_test_batch')


@pytest.mark.parametrize('script', _load_from_assets('reduce-section-'))
def test_reduce_section(module, script):
    section_config = script.get('section', None)
    kwargs = script.get('kwargs', {})
    expected = script.get('expected', {})

    raises = script.get('raises', None)

    # pylint: disable=protected-access
    if raises is not None:
        klass_path = raises['klass'].split('.')
        module_path, klass_name = '.'.join(klass_path[0:-1]), klass_path[-1]

        klass = getattr(sys.modules[module_path], klass_name)

        with pytest.raises(klass, match=raises['match']):
            module._reduce_section(section_config, **kwargs)

    else:
        actual = module._reduce_section(section_config, **kwargs)

        gluetool.log.log_dict(module.debug, 'expected command sets', expected)
        gluetool.log.log_dict(module.debug, 'actual command sets', actual)

        assert actual == expected


@pytest.mark.parametrize('config, expected', [
    (
        {}, {'default': []}
    ),
    (
        {'packages': {}}, {'default': []}
    )
])
def test_config(module, config, expected):
    # pylint: disable=protected-access
    actual = module._construct_command_sets(config, 'component-foo')

    gluetool.log.log_dict(module.debug, 'expected command sets', expected)
    gluetool.log.log_dict(module.debug, 'actual command sets', actual)

    assert actual == expected
