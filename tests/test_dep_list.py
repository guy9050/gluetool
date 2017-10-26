# pylint: disable=protected-access
import pytest

import libci
from libci.modules.helpers.dep_list import ModuleInfoGroup, ModuleInfo, DepList
from . import create_module


@pytest.fixture(name='module')
def fixture_module():
    return create_module(DepList)


@pytest.fixture(name='moduleinfo_group')
def fixture_moduleinfo_group(module):
    _, module = module
    group = ModuleInfoGroup(module)
    return group


def test_shared(module):
    ci, _ = module
    assert ci.has_shared('prepare_dependencies') is True


def test_loadable(module):
    ci, _ = module
    # pylint: disable=protected-access
    python_mod = ci._load_python_module('helpers', 'pytest_deplist',
                                        'libci/modules/helpers/dep_list.py')

    assert hasattr(python_mod, 'DepList')


@pytest.mark.parametrize('only_modules, res', [
    (['dummy1'], ['m1']),
    (['dummy2'], ['m2']),
    (['dummy1', 'dummy2'], ['m1', 'm2']),
    (None, ['m1', 'm2']),
])
def test_only_specified_pip_modules(moduleinfo_group, only_modules, res):
    m1 = ModuleInfo({
        'name': 'dummy1',
        'description': 'dummy1 module',
        'dependencies': {
            'pip': ['m1']
        }
    })
    m2 = ModuleInfo({
        'name': 'dummy2',
        'description': 'dummy2 module',
        'dependencies': {
            'pip': ['m2']
        }
    })
    moduleinfo_group.add_moduleinfo(m1)
    moduleinfo_group.add_moduleinfo(m2)
    deps = moduleinfo_group.get_dependencies(only_modules)
    assert sorted(deps['pip']) == sorted(res)


@pytest.mark.parametrize('dep1, dep2, res', [
    ('m', 'm>1', 'm>1'),
    ('m>=1', 'm>1', 'm>1'),
    ('m>1', 'm>0.9', 'm>1'),
    ('m>=1', 'm<1.1', 'm>=1,<1.1'),
    ('m>=1', 'm<1.1', 'm>=1,<1.1'),
    ('m>1', 'm<=1.1', 'm>1,<=1.1'),
    ('m==1', 'm<=1.1', 'm==1'),
    ('m==1', 'm<1.1', 'm==1'),
    ('m==1', 'm>=0.1', 'm==1'),
    ('m==1', 'm>0.1', 'm==1'),
    ('m==1', 'm==2', None),
    ('m==1', 'm<0.1', None),
    ('m==1', 'm>1.1', None),
    ('m<=1', 'm>1', None),
    ('m>=1', 'm<1', None),
    ('m>1', 'm<1', None),
])
def test_pip_version_sanity(moduleinfo_group, dep1, dep2, res):
    m1 = ModuleInfo({
        'name': 'dummy1',
        'description': 'dummy1 module',
        'dependencies': {
            'pip': [dep1]
        }
    })
    m2 = ModuleInfo({
        'name': 'dummy2',
        'description': 'dummy2 module',
        'dependencies': {
            'pip': [dep2]
        }
    })
    moduleinfo_group.add_moduleinfo(m1)
    moduleinfo_group.add_moduleinfo(m2)
    if res:
        deps = moduleinfo_group.get_dependencies(None)
        assert len(deps['pip']) == 1
        assert deps['pip'][0] == res
    else:
        with pytest.raises(libci.CIError):
            moduleinfo_group.get_dependencies(None)
