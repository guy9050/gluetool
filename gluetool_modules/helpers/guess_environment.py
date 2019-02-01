import re
import collections
import bs4

import gluetool
from gluetool import GlueError
from gluetool.log import log_dict
from gluetool.utils import fetch_url, PatternMap, IncompatibleOptionsError

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import cast, Any, Callable, Dict, List, Optional, Tuple, Type, Union  # noqa


DEFAULT_NIGHTLY_LISTING = 'http://download.eng.brq.redhat.com/nightly/'  # type: str
DEFAULT_BU_LISTING = 'http://download-node-02.eng.bos.redhat.com/rel-eng/updates/'  # type: str


class CIGuess(gluetool.Module):
    """
    "Guess" distro/image/product.
    User can choose from different possible methods of "guessing":

    * ``target-autodetection``: module will transform target artifacts to a
      distro/image/product, using provided regex patterns
      in ``--[distro/image/product]-pattern-map`` file

    * ``force``: use specified distro/image no matter what. Use
      ``--distro``/``--image``/``--product`` option to set *what* goal you wish to use

    * ``recent``: (Only for images) use ``--image`` option as a hint - a regular
      expression, with one matching group, that tells module what image names should be
      considered for selection, and which part of the image name is the key. Images are
      then sorted by their respective key values, and the most recent one is used.
      E.g. ``--image 'Fedora-Cloud-Base-25-(\\d+)\\.\\d'`` will use *date* part
      of image name as a key (e.g. ``20170102``).

    * ``nightly``: (Only for distro) check the nightly composes, and choose the recent available. Use ``--distro``
      option to specify which distro you talk about (e.g. ``RHEL-7.4`` will check RHEL-7.4
      nightlies, and you'll get something like ``RHEL-7.4-20170223.n.0``.

    * ``buc``: (Only for distro) check the batch update composes, and choose the recent
      available. Use ``--distro`` option to specify which distro you talk about
      (e.g. ``RHEL-7.3`` will check RHEL-7.3 composes, and you'll get something like
      ``RHEL-7.3-updates-20170405.0``.
    """

    name = 'guess-environment'
    description = 'Guess distro/image/product from target artifacts'

    options = [
        ('Methods', {
            'distro-method': {
                'help': 'What method to use for distro "guessing" (default: %(default)s).',
                'choices': ('target-autodetection', 'force', 'nightly', 'buc'),
                'default': 'target-autodetection'
            },
            'image-method': {
                'help': 'What method to use for distro "guessing" (default: %(default)s).',
                'choices': ('target-autodetection', 'force', 'recent'),
                'default': 'target-autodetection'
            },
            'product-method': {
                'help': 'What method to use for distro "guessing" (default: %(default)s).',
                'choices': ('target-autodetection', 'force'),
                'default': 'target-autodetection'
            }
        }),
        ('Specifications', {
            'distro': {
                'help': 'Distro specification, to help your method with guessing (default: none).',
                'action': 'append',
                'default': []
            },
            'image': {
                'help': 'Image specification, to help your method with guessing',
            },
            'product': {
                'help': 'Product identification, to help your method with guessing.'
            }
        }),
        ('Distro-listings', {
            'nightly-listing': {
                'help': """
                        URL where list of nightly composes lies, in a form of web server's
                        directory listing (default: %(default)s).
                        """,
                'default': DEFAULT_NIGHTLY_LISTING
            },
            'bu-listing': {
                'help': """
                        URL where list of batch update composes lies, in a form of web server's
                        directory listing (default: %(default)s).
                        """,
                'default': DEFAULT_BU_LISTING
            }
        }),
        ('Pattern-maps', {
            'distro-pattern-map': {
                'help': 'Path to a file with target => distro patterns.'
            },
            'image-pattern-map': {
                'help': 'Path to a file with target => image patterns.'
            },
            'product-pattern-map': {
                'help': 'Path to a file with target => product patterns.'
            }
        })
    ]

    shared_functions = ['distro', 'image', 'product']

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    def __init__(self, *args, **kwargs):
        # type: (Any, Any) -> None
        super(CIGuess, self).__init__(*args, **kwargs)

        self._distro = {}  # type: Dict[str, Union[str, List[str]]]
        self._image = {}  # type: Dict[str, Union[str, List[str]]]
        self._product = {}  # type: Dict[str, Union[str, List[str]]]

    def distro(self):
        # type: () -> Union[str, List[str]]
        """
        Return guessed distro value

        :rtype: Union[str, List[str]]
        """
        if self._distro['result'] is None:
            self.execute_method(self._distro)
        return self._distro['result']

    def image(self):
        # type: () -> Union[str, List[str]]
        """
        Return guessed image name

        :rtype: Union[str, List[str]]
        """
        if self._image['result'] is None:
            self.execute_method(self._image)
        return self._image['result']

    def product(self):
        # type: () -> Union[str, List[str]]
        """
        Return guessed product.

        :rtype: Union[str, List[str]]
        """
        if self._product['result'] is None:
            self.execute_method(self._product)
        return self._product['result']

    def pattern_map(self, source):
        # type (Dict[str, Union[str, List[str]]]) -> PatternMap
        def _create_buc_repl(hint_repl):
            # type: (Any) -> Any
            def _replace(pattern, target):
                # type: (Any, Any) -> Any
                """
                Use `hint_repl` function - which was created by `_create_simple_repl` - to get
                a hint which is then used to find out the batch update compose.
                """

                hint = hint_repl(pattern, target)
                self.debug("hint is '{}'".format(hint))

                return self._find_buc_for_distro(hint)

            return _replace

        def _create_nightly_repl(hint_repl):
            # type: (Any) -> Any
            def _replace(pattern, target):
                # type: (Any, Any) -> Any
                """
                Use `hint_repl` function - which was created by `_create_simple_repl` - to get
                a hint which is then used to find out the nightly compose.
                """

                hint = hint_repl(pattern, target)
                self.debug("hint is '{}'".format(hint))

                return self._find_nightly_for_distro(hint)

            return _replace

        spices = {'BUC': _create_buc_repl,
                  'NIGHTLY': _create_nightly_repl} if source['type'] == "distro" else None

        return PatternMap(source['pattern-map'],
                          allow_variables=True,
                          spices=spices,
                          logger=self.logger)

    def _get_latest_finished_compose(self, base_url, hint):
        # type: (str, str) -> Optional[str]
        """
        Fetch index page listing several composes from BASE_URL, and try to find
        the most recent and FINISHED one, using HINT to limit set of examined
        composes - if the composes starts with HINT, we'll check it.

        :param str base_url: URL of the index page. It should be a directory listing,
          with links leading to relevant composes.
        :param str hint: what composes should be examined - it the name of compose
          starts with `hint`, it's one of ours.
        :returns: name of the compose, or `None`.
        """

        # Fetch the index
        try:
            _, content = fetch_url(base_url, logger=self.logger)

        except GlueError:
            raise GlueError('Cannot get list of available composes at {}'.format(base_url))

        # Find all <a/> elements from the index
        soup = bs4.BeautifulSoup(content, 'html.parser')

        # [(text, href), ...]
        composes = [(link.string.replace('/', ''), link['href'])
                    for link in soup.find_all('a') if link.string.startswith(hint)]

        log_dict(self.debug, 'available composes:', composes)

        for name, href in sorted(composes, key=lambda x: x[0], reverse=True):
            self.debug("checking status of '{}'".format(name))

            # Check compose status
            url = '{}/{}/STATUS'.format(base_url, href)

            try:
                _, content = fetch_url(url, logger=self.logger)

            except GlueError:
                self.warn("Cannot find out status of '{}'".format(name))
                continue

            if content.strip() != 'FINISHED':
                self.debug("'{}' not marked as finished".format(name))
                continue

            # Get its ID
            url = '{}/{}/COMPOSE_ID'.format(base_url, href)

            try:
                _, content = fetch_url(url, logger=self.logger)

            except GlueError:
                self.warn("Cannot find out ID of '{}'".format(name))
                continue

            return content.strip()

        return None

    def _find_buc_for_distro(self, hint):
        # type: (str) -> str
        """
        Find batch update compose for a given distro.

        :param str hint: Values like "RHEL-7.3", "RHEL-6.8", etc.
        :returns: BU compose name.
        """

        self.debug("Looking for latest valid BU compose for '{}'".format(hint))

        # First, try to take "latest-FOO" shortcut
        url = self.option('bu-listing') + '/latest-{}'.format(hint) + '/COMPOSE_ID'

        try:
            _, content = fetch_url(url, logger=self.logger)
            return content.strip()

        except GlueError:
            self.warn("Cannot find shortcut '/latest-{}'".format(hint))

        # Ok, so there's no "/latest-<hint>" directory, lets iterate over all available composes
        # under "/<hint>"
        distro = self._get_latest_finished_compose('{}/{}'.format(self.option('bu-listing'), hint), hint)

        if distro is None:
            raise GlueError('None of examined BU composes was acceptable')

        return distro

    def _find_nightly_for_distro(self, hint):
        # type: (str) -> str
        """
        Find nightly compose for a give distro.

        :param str hint: Values like "RHEL-7.3", "RHEL-6.8", etc.
        :returns: Nightly compose name.
        """

        self.debug("Looking for latest valid nightly compose for '{}'".format(hint))

        distro = self._get_latest_finished_compose(self.option('nightly-listing'), hint)

        if distro is None:
            raise GlueError('None of examined nightly composes was acceptable')

        return distro

    def _guess_recent(self, source):
        # type: (Dict[str, Union[str, List[str]]]) -> None
        self.require_shared('openstack')

        hint = '^{}$'.format(source['specification'])
        self.debug("using pattern '{}' as a hint".format(hint))

        try:
            hint_pattern = re.compile(hint)

        except re.error as exc:
            raise GlueError("cannot compile hint pattern '{}': {}".format(hint, str(exc)))

        possible_image = collections.namedtuple('possible_image', ['key', 'name'])
        possible_images = []

        for image in self.shared('openstack').images.list():
            match = hint_pattern.match(image.name)
            if not match:
                continue

            try:
                possible_images.append(possible_image(key=match.group(1), name=image.name))

            except IndexError:
                raise GlueError("Cannot deduce the key from image name '{}'".format(image.name))

        if not possible_images:
            raise GlueError("No image found for hint '{}'".format(hint))

        log_dict(self.debug, 'possible images', possible_images)

        source['result'] = sorted(possible_images, key=lambda x: x.key)[-1].name

    def _guess_nightly(self, source):
        # type: (Dict[str, Union[str, List[str]]]) -> None
        source['result'] = [
            self._find_nightly_for_distro(s.strip()) for s in source['specification']
        ]

    def _guess_buc(self, source):
        # type: (Dict[str, Union[str, List[str]]]) -> None
        source['result'] = [
            self._find_buc_for_distro(s.strip()) for s in source['specification']
        ]

    @staticmethod
    def _guess_force(source):
        # type: (Dict[str, Union[str, List[str]]]) -> None
        if source['type'] == 'distro':
            source['result'] = [s.strip() for s in source['specification']]

        else:
            source['result'] = source['specification']

    def _guess_target_autodetection(self, source):
        # type: (Dict[str, Union[str, List[str]]]) -> None
        self.require_shared('primary_task')

        target = self.shared('primary_task').target

        source['result'] = self.pattern_map(source).match(target, multiple=(source['type'] == 'distro'))

    _methods = {
        'force': _guess_force,
        'target-autodetection': _guess_target_autodetection,
        'recent': _guess_recent,  # Only for images
        'nightly': _guess_nightly,  # Only for distro
        'buc': _guess_buc  # Only for distro
    }

    def _pack_sources(self):
        """
        Packs necessary for guessing values to dict.
        This solution provides the same parameters for guessing methods
        what makes guessing methods universal for all types of guessing target
        """
        self._distro = {
            'type': 'distro',
            'specification': self.option('distro'),
            'method': self.option('distro-method'),
            'pattern-map': self.option('distro-pattern-map'),
            'result': None
        }
        self._image = {
            'type': 'image',
            'specification': self.option('image'),
            'method': self.option('image-method'),
            'pattern-map': self.option('image-pattern-map'),
            'result': None
        }
        self._product = {
            'type': 'product',
            'specification': self.option('product'),
            'method': self.option('product-method'),
            'pattern-map': self.option('product-pattern-map'),
            'result': None
        }

    def sanity(self):
        # type: () -> None

        # Packs sources here, because self.option is unavailable in __init__
        self._pack_sources()

        specification_required = ('force', 'recent', 'nightly', 'buc')
        specification_ignored = ('target-autodetection',)

        for source in [self._distro, self._image, self._product]:

            if source['method'] == 'target-autodetection' and source['pattern-map'] is None:
                raise GlueError(
                    "--{}-pattern-map option is required with method '{}'".format(
                        source['type'], source['method']))

            if source['method'] in specification_required and source['specification'] is None:
                raise IncompatibleOptionsError(
                    "--{} option is required with method '{}'".format(source['type'], source['method']))

            if source['method'] in specification_ignored and source['specification'] not in [None, []]:
                raise IncompatibleOptionsError(
                    "--{} option is ignored with method '{}'".format(source['type'], source['method']))

    def execute_method(self, source):
        # type: (Dict[str, Union[str, List[str]]]) -> None

        method = self._methods.get(source['method'], None)  # type: ignore
        if method is None:
            raise IncompatibleOptionsError("Unknown 'guessing' method '{}'".format(source['method']))

        method(self, source)

        log_dict(self.info, 'Using {}'.format(source['type']), source['result'])
