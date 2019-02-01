import pytest

from mock import MagicMock

import gluetool
import gluetool_modules.helpers.guess_environment

from . import create_module, patch_shared, assert_shared


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument
    module = create_module(gluetool_modules.helpers.guess_environment.CIGuess)[1]
    # pylint: disable=protected-access
    module._distro = {
        'type': 'distro',
        'specification': 'foo',
        'method': 'foo',
        'pattern-map': 'foo',
        'result': None
    }
    module._image = {
        'type': 'image',
        'specification': 'foo',
        'method': 'foo',
        'pattern-map': 'foo',
        'result': None
    }
    module._product = {
        'type': 'product',
        'specification': 'foo',
        'method': 'foo',
        'pattern-map': 'foo',
        'result': None
    }
    return module


@pytest.fixture(name='module_for_recent')
def fixture_module_for_recent(module, monkeypatch):
    # `name` is an argument to the Mock constructor...
    def _image(name):
        mock_image = MagicMock()
        mock_image.name = name

        return mock_image

    images = [
        _image('image-20160107'),
        _image('image-20160109'),
        _image('image-foo'),
        _image('image-20160103')
    ]

    patch_shared(monkeypatch, module, {
        'openstack': MagicMock(images=MagicMock(list=MagicMock(return_value=images)))
    })

    # pylint: disable=protected-access
    module._image = {
        'type': 'image',
        'specification': r'image-(\d+)',
        'method': 'recent',
        'pattern-map': 'foo',
        'result': None
    }
    return module


def test_loadable(module):
    # pylint: disable=protected-access
    python_mod = module.glue._load_python_module('helpers', 'pytest_guess_environment',
                                                 'gluetool_modules/helpers/guess_environment.py')

    assert hasattr(python_mod, 'CIGuess')


@pytest.mark.parametrize('method, image, raises_exc, use', [
    ('recent', None, True, 'required'),
    ('recent', 'foo', False, None),
    ('force', None, True, 'required'),
    ('force', 'foo', False, None),
    ('target-autodetection', None, False, None),
    ('target-autodetection', 'foo', True, 'ignored')
])
def test_method_image_match(module, method, image, raises_exc, use):
    # pylint: disable=protected-access
    module._config['image-method'] = method
    module._config['image'] = image
    module._config['image-pattern-map'] = 'foo'

    if raises_exc:
        with pytest.raises(gluetool.GlueError, match=r"^--image option is %s with method '%s'$" % (use, method)):
            module.sanity()

    else:
        module.sanity()


@pytest.mark.parametrize('method, distro, raises_exc, use', [
    ('nightly', None, True, 'required'),
    ('nightly', 'foo', False, None),
    ('buc', None, True, 'required'),
    ('buc', 'foo', False, None),
    ('force', None, True, 'required'),
    ('force', 'foo', False, None),
    ('target-autodetection', None, False, None),
    ('target-autodetection', 'foo', True, 'ignored')
])
def test_method_distro_match(module, method, distro, raises_exc, use):
    # pylint: disable=protected-access
    module._config['distro-method'] = method
    module._config['distro'] = distro
    module._config['distro-pattern-map'] = 'foo'

    if raises_exc:
        with pytest.raises(gluetool.GlueError, match=r"^--distro option is %s with method '%s'$" % (use, method)):
            module.sanity()

    else:
        module.sanity()


@pytest.mark.parametrize('method, product, raises_exc, use', [
    ('force', None, True, 'required'),
    ('force', 'foo', False, None),
    ('target-autodetection', None, False, None),
    ('target-autodetection', 'foo', True, 'ignored')
])
def test_method_product_match(module, method, product, raises_exc, use):
    # pylint: disable=protected-access
    module._config['product-method'] = method
    module._config['product'] = product
    module._config['product-pattern-map'] = 'foo'

    if raises_exc:
        with pytest.raises(gluetool.GlueError, match=r"^--product option is %s with method '%s'$" % (use, method)):
            module.sanity()

    else:
        module.sanity()


@pytest.mark.parametrize('pattern_map, raises_exc', [
    (None, True),
    ('foo', False)
])
def test_image_method_pattern_map_match(module, pattern_map, raises_exc):
    # pylint: disable=protected-access
    module._config['image-method'] = 'target-autodetection'
    module._config['image-pattern-map'] = pattern_map

    if raises_exc:
        # pylint: disable=line-too-long
        with pytest.raises(gluetool.GlueError, match=r"^--image-pattern-map option is required with method 'target-autodetection'$"):
            module.sanity()

    else:
        module.sanity()


@pytest.mark.parametrize('pattern_map, raises_exc', [
    (None, True),
    ('foo', False)
])
def test_distro_method_pattern_map_match(module, pattern_map, raises_exc):
    # pylint: disable=protected-access
    module._config['distro-method'] = 'target-autodetection'
    module._config['distro-pattern-map'] = pattern_map

    if raises_exc:
        # pylint: disable=line-too-long
        with pytest.raises(gluetool.GlueError, match=r"^--distro-pattern-map option is required with method 'target-autodetection'$"):
            module.sanity()

    else:
        module.sanity()


@pytest.mark.parametrize('pattern_map, raises_exc', [
    (None, True),
    ('foo', False)
])
def test_product_method_pattern_map_match(module, pattern_map, raises_exc):
    # pylint: disable=protected-access
    module._config['product-method'] = 'target-autodetection'
    module._config['product-pattern-map'] = pattern_map

    if raises_exc:
        # pylint: disable=line-too-long
        with pytest.raises(gluetool.GlueError, match=r"^--product-pattern-map option is required with method 'target-autodetection'$"):
            module.sanity()

    else:
        module.sanity()


def test_shared_image(module):
    # pylint: disable=protected-access
    module._image['result'] = MagicMock()

    assert module.image() == module._image['result']


def test_shared_distro(module):
    # pylint: disable=protected-access
    module._distro['result'] = MagicMock()

    assert module.distro() == module._distro['result']


def test_shared_product(module):
    # pylint: disable=protected-access
    module._product['result'] = MagicMock()

    assert module.product() == module._product['result']


def test_image_pattern_map(module, monkeypatch):
    map_instance = MagicMock()
    map_class = MagicMock(return_value=map_instance)

    monkeypatch.setattr(gluetool_modules.helpers.guess_environment, 'PatternMap', map_class)

    # pylint: disable=protected-access
    module._image['pattern-map'] = 'dummy-map.yml'

    assert module.pattern_map(module._image) == map_instance
    map_class.assert_called_once_with('dummy-map.yml', allow_variables=True,
                                      spices=None, logger=module.logger)


def test_distro_pattern_map(module, monkeypatch):
    map_instance = MagicMock()
    map_class = MagicMock(return_value=map_instance)

    monkeypatch.setattr(gluetool_modules.helpers.guess_environment, 'PatternMap', map_class)

    # pylint: disable=protected-access
    module._distro['pattern-map'] = 'dummy-map.yml'


def test_product_pattern_map(module, monkeypatch):
    map_instance = MagicMock()
    map_class = MagicMock(return_value=map_instance)

    monkeypatch.setattr(gluetool_modules.helpers.guess_environment, 'PatternMap', map_class)

    # pylint: disable=protected-access
    module._product['pattern-map'] = 'dummy-map.yml'

    assert module.pattern_map(module._product) == map_instance
    map_class.assert_called_once_with('dummy-map.yml', allow_variables=True,
                                      spices=None, logger=module.logger)


def test_image_force(module):
    image = 'dummy-image'

    # pylint: disable=protected-access
    module._image['specification'] = image

    module._guess_force(module._image)

    assert module._image['result'] == image


def test_distro_force(module):
    distro = 'dummy-distro'

    # pylint: disable=protected-access
    module._distro['specification'] = [distro]

    module._guess_force(module._distro)

    assert module._distro['result'] == [distro]


def test_product_force(module):
    product = 'dummy-product'

    # pylint: disable=protected-access
    module._product['specification'] = product

    module._guess_force(module._product)

    assert module._product['result'] == product


def test_image_autodetection(module, monkeypatch):
    target = 'dummy-target'
    image = 'dummy-image'

    # pylint: disable=protected-access
    module._image['method'] = 'target-autodetection'

    monkeypatch.setattr(module.glue, 'shared_functions', {
        'primary_task': (None, MagicMock(return_value=MagicMock(target=target)))
    })

    # monkeypatching of @cached_property does not work, the property's __get__() gets called...
    module.pattern_map = MagicMock(return_value=MagicMock(match=MagicMock(return_value=image)))

    # pylint: disable=protected-access
    module._guess_target_autodetection(module._image)


def test_distro_autodetection(module, monkeypatch):
    target = 'dummy-target'
    distro = 'dummy-distro'

    # pylint: disable=protected-access
    module._distro['method'] = 'target-autodetection'

    monkeypatch.setattr(module.glue, 'shared_functions', {
        'primary_task': (None, MagicMock(return_value=MagicMock(target=target)))
    })

    # monkeypatching of @cached_property does not work, the property's __get__() gets called...
    module.pattern_map = MagicMock(return_value=MagicMock(match=MagicMock(return_value=distro)))

    # pylint: disable=protected-access
    module._guess_target_autodetection(module._distro)


def test_product_autodetection(module, monkeypatch):
    target = 'dummy-target'
    product = 'dummy-product'

    # pylint: disable=protected-access
    module._product['method'] = 'target-autodetection'

    monkeypatch.setattr(module.glue, 'shared_functions', {
        'primary_task': (None, MagicMock(return_value=MagicMock(target=target)))
    })

    # monkeypatching of @cached_property does not work, the property's __get__() gets called...
    module.pattern_map = MagicMock(return_value=MagicMock(match=MagicMock(return_value=product)))

    # pylint: disable=protected-access
    module._guess_target_autodetection(module._product)


def test_autodetection_no_brew(module):
    # pylint: disable=protected-access
    assert_shared('primary_task', module._guess_target_autodetection, MagicMock())


def test_recent_no_openstack(module):
    # pylint: disable=protected-access
    assert_shared('openstack', module._guess_recent, MagicMock())


def test_recent_broken_regexp(monkeypatch, module):
    module.has_shared = MagicMock(return_value=True)

    patch_shared(monkeypatch, module, {
        'openstack': None
    })

    # pylint: disable=protected-access
    module._image['specification'] = '[foo'
    module._image['method'] = 'recent'

    # pylint: disable=line-too-long
    with pytest.raises(gluetool.GlueError, match=r"cannot compile hint pattern '\^\[foo\$': unexpected end of regular expression"):
        module._guess_recent(module._image)


def test_recent(module_for_recent):
    # pylint: disable=protected-access
    module_for_recent._guess_recent(module_for_recent._image)

    assert module_for_recent._image['result'] == 'image-20160109'


def test_recent_no_match(module_for_recent):
    # pylint: disable=protected-access
    module_for_recent._image['specification'] = r'foo-(\d+)'

    with pytest.raises(gluetool.GlueError, match=r"No image found for hint '\^foo-\(\\d\+\)\$'"):
        module_for_recent._guess_recent(module_for_recent._image)


def test_recent_no_key(module_for_recent):
    # pylint: disable=protected-access
    module_for_recent._image['specification'] = r'image-foo'

    with pytest.raises(gluetool.GlueError, match=r" key from image name 'image-foo'"):
        module_for_recent._guess_recent(module_for_recent._image)


def test_execute_unknown_method(module):
    # pylint: disable=protected-access
    with pytest.raises(gluetool.GlueError, match=r"Unknown 'guessing' method 'foo'"):
        module.execute_method(module._image)


def test_execute(module, log):
    def _guess_foo(self, source):
        # pylint: disable=protected-access,unused-argument
        source['result'] = 'dummy'

    guess_foo = MagicMock(side_effect=_guess_foo)

    # pylint: disable=protected-access
    module._methods['foo'] = guess_foo

    module.distro()
    module.image()
    module.product()

    assert module._distro['result'] == 'dummy'
    assert log.records[-3].message == "Using distro:\n\"dummy\""
    assert module._image['result'] == 'dummy'
    assert log.records[-2].message == "Using image:\n\"dummy\""
    assert module._product['result'] == 'dummy'
    assert log.records[-1].message == "Using product:\n\"dummy\""
