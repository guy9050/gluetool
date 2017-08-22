import pytest

from mock import MagicMock

import libci
import libci.modules.helpers.guess_openstack_image

from . import create_module


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.helpers.guess_openstack_image.GuessOpenstackImage)[1]


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

    def mock_shared(name, *args, **kwargs):
        # pylint: disable=unused-argument

        return {
            'openstack': MagicMock(images=MagicMock(list=MagicMock(return_value=images)))
        }[name]

    monkeypatch.setattr(module, 'has_shared', MagicMock(return_value=True))
    monkeypatch.setattr(module, 'shared', mock_shared)

    # pylint: disable=protected-access
    module._config['method'] = 'recent'
    module._config['image'] = r'image-(\d+)'

    return module


def test_loadable(module):
    # pylint: disable=protected-access
    python_mod = module.ci._load_python_module('helpers', 'pytest_guess_openstack_module',
                                               'libci/modules/helpers/guess_openstack_image.py')

    assert hasattr(python_mod, 'GuessOpenstackImage')


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
    module._config['method'] = method
    module._config['image'] = image
    module._config['pattern-map'] = 'foo'

    if raises_exc:
        with pytest.raises(libci.CIError, match=r"^--image option is %s with method '%s'$" % (use, method)):
            module.sanity()

    else:
        module.sanity()


@pytest.mark.parametrize('pattern_map, raises_exc', [
    (None, True),
    ('foo', False)
])
def test_method_pattern_map_match(module, pattern_map, raises_exc):
    # pylint: disable=protected-access
    module._config['method'] = 'target-autodetection'
    module._config['pattern-map'] = pattern_map

    if raises_exc:
        # pylint: disable=line-too-long
        with pytest.raises(libci.CIError, match=r"^--pattern-map option is required with method 'target-autodetection'$"):
            module.sanity()

    else:
        module.sanity()


def test_shared_image(module):
    # pylint: disable=protected-access
    module._image = MagicMock()

    assert module.image() == module._image


def test_pattern_map(module, monkeypatch):
    map_instance = MagicMock()
    map_class = MagicMock(return_value=map_instance)

    monkeypatch.setattr(libci.modules.helpers.guess_openstack_image, 'PatternMap', map_class)

    # pylint: disable=protected-access
    module._config['pattern-map'] = 'dummy-map.yml'

    assert module.pattern_map == map_instance
    map_class.assert_called_once_with('dummy-map.yml', logger=module.logger)


def test_force(module, log):
    image = 'dummy-image'

    # pylint: disable=protected-access
    module._config['method'] = 'force'
    module._config['image'] = image

    module._guess_force()

    assert module._image == image
    assert log.records[-1].message == "forcing '{}' as an image".format(image)


def test_autodetection(module, log, monkeypatch):
    target = 'dummy-target'
    image = 'dummy-image'

    def mock_shared(name, *args, **kwargs):
        # pylint: disable=unused-argument

        return {
            'task': MagicMock(target=MagicMock(target=target))
        }[name]

    monkeypatch.setattr(module, 'shared', mock_shared)
    # monkeypatching of @cached_property does not work, the property's __get__() gets called...
    module.pattern_map = MagicMock(match=MagicMock(return_value=image))

    # pylint: disable=protected-access
    module._guess_target_autodetection()

    assert log.records[-1].message == "transformed target '{}' to the image '{}'".format(target, image)
    module.pattern_map.match.assert_called_once_with(target)


def test_autodetection_no_brew(module):
    with pytest.raises(libci.CIError, match=r"Using 'target-autodetect' method without a brew task does not work"):
        # pylint: disable=protected-access
        module._guess_target_autodetection()


def test_recent_no_openstack(module):
    # pylint: disable=line-too-long
    with pytest.raises(libci.CIError, match=r"Module requires OpenStack connection, provided e.g. by the 'openstack' module"):
        # pylint: disable=protected-access
        module._guess_recent()


def test_recent_broken_regexp(module):
    module.has_shared = MagicMock(return_value=True)

    # pylint: disable=protected-access
    module._config['image'] = '[foo'

    # pylint: disable=line-too-long
    with pytest.raises(libci.CIError, match=r"cannot compile hint pattern '\^\[foo\$': unexpected end of regular expression"):
        module._guess_recent()


def test_recent(module_for_recent):
    # pylint: disable=protected-access
    module_for_recent._guess_recent()

    assert module_for_recent._image == 'image-20160109'


def test_recent_no_match(module_for_recent):
    # pylint: disable=protected-access
    module_for_recent._config['image'] = r'foo-(\d+)'

    with pytest.raises(libci.CIError, match=r"No image found for hint '\^foo-\(\\d\+\)\$'"):
        module_for_recent._guess_recent()


def test_recent_no_key(module_for_recent):
    # pylint: disable=protected-access
    module_for_recent._config['image'] = r'image-foo'

    with pytest.raises(libci.CIError, match=r" key from image name 'image-foo'"):
        module_for_recent._guess_recent()


def test_execute_unknown_method(module):
    # pylint: disable=protected-access
    module._config['method'] = 'foo'

    with pytest.raises(libci.CIError, match=r"Unknown 'guessing' method 'foo'"):
        module.execute()


def test_execute(module, log):
    def _guess_foo(*args, **kwargs):
        # pylint: disable=protected-access,unused-argument
        module._image = 'dummy-image'

    guess_foo = MagicMock(side_effect=_guess_foo)

    # pylint: disable=protected-access
    module._config['method'] = 'foo'
    module._methods['foo'] = guess_foo

    module.execute()

    guess_foo.assert_called_once()
    assert module._image == 'dummy-image'
    assert log.records[-1].message == "Using image 'dummy-image'"
