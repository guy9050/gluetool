import collections
import re

from libci import CIError, SoftCIError, Module
from libci.log import format_dict
from libci.utils import cached_property, PatternMap


class IncompatibleOptionsError(SoftCIError):
    SUBJECT = 'Incompatible options detected'
    BODY = """
Configuration of your component uses incompatible options for `guess-openstack-image` module:

    {message}

Please, review the configuration of your component - the default settings are usually sane
and should not lead to this error. For valid options, their values and possible combinations
see documentation for `guess-openstack-image` ([1]).

[1] https://url.corp.redhat.com/9249e74
    """


class GuessOpenstackImage(Module):
    """
    "Guess" openstack image. User can choose from different possible methods of "guessing":

    * ``target-autodetection``: module will try to transform build target of brew task to an image name
      using provided regex patterns in ``--pattern-map`` file;

    * ``force``: use specified image no matter what. Use ``--image`` option to set *what*
      image you wish to use;

    * ``recent``: use ``--image`` option as a hint - a regular expression, with one matching group,
      that tells module what image names should be considered for selection, and which part of the
      image name is the key. Images are then sorted by their respective key values, and the most
      recent one is used. E.g. ``--image 'Fedora-Cloud-Base-25-(\\d+)\\.\\d'`` will use *date* part
      of image name as a key (e.g. ``20170102``).
    """

    name = 'guess-openstack-image'
    description = 'Guess openstack image from build target of a brew build'

    options = {
        'method': {
            'help': 'What method to use for image "guessing"',
            'choices': ('target-autodetection', 'force', 'recent'),
            'default': 'target-autodetection'
        },
        'image': {
            'help': 'Image specification, to help your method with guessing'
        },
        'list-images': {
            'help': 'List all available images',
        },
        'pattern-map': {
            'help': 'Path to a file with target => image patterns.'
        }
    }

    shared_functions = ['image']

    def __init__(self, *args, **kwargs):
        super(GuessOpenstackImage, self).__init__(*args, **kwargs)

        self._image = None

    def image(self):
        """ return guessed image name """
        return self._image

    @cached_property
    def pattern_map(self):
        return PatternMap(self.option('pattern-map'), logger=self.logger)

    def _guess_force(self):
        image = self.option('image')
        self.debug("forcing '{}' as an image".format(image))

        self._image = image

    def _guess_recent(self):
        if not self.has_shared('openstack'):
            raise CIError("Module requires OpenStack connection, provided e.g. by the 'openstack' module")

        hint = '^{}$'.format(self.option('image'))
        self.debug("using pattern '{}' as a hint".format(hint))

        try:
            hint_pattern = re.compile(hint)

        except re.error as exc:
            raise CIError("cannot compile hint pattern '{}': {}".format(hint, str(exc)))

        possible_image = collections.namedtuple('possible_image', ['key', 'name'])
        possible_images = []

        for image in self.shared('openstack').images.list():
            match = hint_pattern.match(image.name)
            if not match:
                continue

            try:
                possible_images.append(possible_image(key=match.group(1), name=image.name))

            except IndexError:
                raise CIError("Cannot deduce the key from image name '{}'".format(image.name))

        if not possible_images:
            raise CIError("No image found for hint '{}'".format(hint))

        self.debug('possible images:\n{}'.format(format_dict(possible_images)))

        self._image = sorted(possible_images, key=lambda x: x.key)[-1].name

    def _guess_target_autodetection(self):
        task = self.shared('task')
        if task is None:
            raise CIError("Using 'target-autodetect' method without a brew task does not work")

        target = task.target

        self._image = self.pattern_map.match(target)
        self.debug("transformed target '{}' to the image '{}'".format(target, self._image))

    _methods = {
        'force': _guess_force,
        'target-autodetection': _guess_target_autodetection,
        'recent': _guess_recent
    }

    def sanity(self):
        image_required = ('force', 'recent')
        image_ignored = ('target-autodetection',)

        method = self.option('method')
        image = self.option('image')

        if method == 'target-autodetection' and not self.option('pattern-map'):
            raise CIError("--pattern-map option is required with method '{}'".format(method))

        if method in image_required and not image:
            raise IncompatibleOptionsError("--image option is required with method '{}'".format(method))

        if method in image_ignored and image:
            raise IncompatibleOptionsError("--image option is ignored with method '{}'".format(method))

    def execute(self):
        method = self._methods.get(self.option('method'), None)
        if method is None:
            raise IncompatibleOptionsError("Unknown 'guessing' method '{}'".format(self.option('method')))

        method(self)
        self.info("Using image '{}'".format(self._image))
