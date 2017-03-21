import re
from collections import OrderedDict
from libci import CIError, Module


class CIGuessOpenstackImage(Module):
    """
    "Guess" openstack image. User can choose from different possible methods of "guessing":

      - 'target-autodetection': module will transform build target of brew task to an image name,
      e.g. 'rhel-7.3-candidate' => 'rhel-7.3-server-x86_64-released'. This is the default method.
      The z-stream/eus/aus build targets are translated to *-updated images.

      - 'force': use specified image no matter what. Use --image option to set *what*
      image you wish to use
    """

    name = 'guess-openstack-image'
    description = 'Guess openstack image from build target of a brew build'

    options = {
        'method': {
            'help': 'What method to use for image "guessing"',
            'default': 'target-autodetection'
        },
        'image': {
            'help': 'Image specification, to help your method with guessing'
        },
        'list-images': {
            'help': 'List all available images',
        }
    }

    shared_functions = ['image']

    _image = None

    def image(self):
        """ return guessed image name """
        return self._image

    def _guess_force(self):
        image = self.option('image')
        self.debug("forcing '{}' as an image".format(image))

        self._image = image

    def _guess_target_autodetection(self):
        task = self.shared('brew_task')
        if task is None:
            raise CIError("Using 'target-autodetect' method without a brew task does not work")

        translations = OrderedDict([
            # for rhel-7.4 and rhel-6.9
            # note: we need to find out this automatically via pp.engineering maybe?
            (r'staging-rhel-6-candidate', lambda match: 'rhel-6.8-server-x86_64-updated'),
            (r'rhel-7.4-candidate', lambda match: 'rhel-7.3-server-x86_64-updated'),
            (r'rhel-6.9-candidate', lambda match: 'rhel-6.8-server-x86_64-updated'),
            # default translation for non-eus/aus/z-stream rhel and staging branches
            (r'(rhel-[0-9]+.[0-9]+)-candidate',
                lambda match: '{}-server-x86_64-released'.format(match.group(1))),
            # eus/aus/z-stream translate always to *-updated
            (r'(rhel-[0-9]+.[0-9]+)-z-candidate',
                lambda match: '{}-server-x86_64-updated'.format(match.group(1))),
        ])

        for regex, function in translations.items():
            match = re.match(regex, task.target.target)
            if match:
                self._image = function(match)
                break
        else:
            raise CIError("could not translate build target '{}' to image".format(task.target.target))

        self.debug("transformed target '{}' to image '{}'".format(task.target.target, self._image))

    _methods = {
        'force': _guess_force,
        'target-autodetection': _guess_target_autodetection,
    }

    def sanity(self):
        image_required = ('force',)
        image_ignored = ('target-autodetection',)

        method = self.option('method')
        image = self.option('image')

        if method in image_required and not image:
            raise CIError("--image option is required with method '{}'".format(method), soft=True)

        if method in image_ignored and image:
            raise CIError("--image option is ignored with method '{}'".format(method), soft=True)

    def execute(self):
        method = self._methods.get(self.option('method'), None)
        if method is None:
            raise CIError("Unknown 'guessing' method '{}'".format(self.option('method')), soft=True)

        method(self)
        self.info("Using image '{}'".format(self._image))
