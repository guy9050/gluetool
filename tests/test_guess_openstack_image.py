import collections
import pytest

import libci
import libci.modules.helpers.guess_openstack_image

from . import NonLoadingCI


# Mock OpenStack machinery - <client>.images interface
MockImage = collections.namedtuple('MockImage', ('name'))


class MockOpenstackImages(object):
    # pylint: disable=too-few-public-methods

    def __init__(self, images):
        self._images = images

    def list(self):
        return self._images


class MockOpenstack(object):
    # pylint: disable=too-few-public-methods

    def __init__(self, images):
        self.images = MockOpenstackImages(images)


def inject_openstack(ci, module, images):
    """
    guess-openstack-image requires openstack connection - mock the necessary
    shared method using given list of availabel images.
    """

    def _openstack():
        return MockOpenstack(images)

    module.openstack = _openstack
    ci.add_shared('openstack', module)


def create_module(module_class, ci_class=NonLoadingCI):
    ci = ci_class()
    mod = module_class(ci)
    mod.add_shared()

    return ci, mod


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.helpers.guess_openstack_image.CIGuessOpenstackImage)


def test_recent_sanity(module):
    _, mod = module

    # pylint: disable=protected-access
    mod._config['method'] = 'recent'

    # this should fail...
    assert mod._config['image'] is None
    with pytest.raises(libci.CIError, match=r"^--image option is required with method 'recent'$"):
        mod.sanity()

    # ... and this should not.
    mod._config['image'] = 'foo'
    mod.sanity()


def test_recent_execute(module):
    ci, mod = module

    inject_openstack(ci, mod, [
        MockImage(name='image-20160107'),
        MockImage(name='image-20160109'),
        MockImage(name='image-20160103')
    ])

    # pylint: disable=protected-access
    mod._config['method'] = 'recent'
    mod._config['image'] = r'image-(\d+)'

    assert mod._image is None
    mod.execute()
    assert mod._image == 'image-20160109'


def test_recent_broken_regexp(module):
    """
    Check whether module handles broken hints, e.g. missing parenthesis.
    """

    _, mod = module

    # pylint: disable=protected-access
    mod._config['image'] = '[foo'

    # pylint: disable=line-too-long
    with pytest.raises(libci.CIError, match=r"cannot compile hint pattern '\^\[foo\$': unexpected end of regular expression"):
        mod._guess_recent()


def test_recent_no_openstack(module):
    """
    Check whether module handles unavailable OpenStack connection.
    """

    _, mod = module

    # pylint: disable=protected-access
    mod._config['image'] = 'foo'

    # pylint: disable=line-too-long
    with pytest.raises(libci.CIError, match=r"Module requires OpenStack connection, provided e.g. by the 'openstack' module"):
        mod._guess_recent()


def test_recent_no_match(module):
    """
    No image matches the hint.
    """

    ci, mod = module

    inject_openstack(ci, mod, [])

    # pylint: disable=protected-access
    mod._config['image'] = 'foo'

    with pytest.raises(libci.CIError, match=r"No image found for hint '\^foo\$'"):
        mod._guess_recent()


def test_recent_match(module):
    """
    Common workflow.
    """

    ci, mod = module

    inject_openstack(ci, mod, [
        MockImage(name='image-20160107'),
        MockImage(name='image-20160109'),
        MockImage(name='image-20160103')
    ])

    # pylint: disable=protected-access
    mod._config['image'] = r'image-(\d+)'

    assert mod._image is None
    mod._guess_recent()
    assert mod._image == 'image-20160109'
