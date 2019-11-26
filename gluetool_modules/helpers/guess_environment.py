import bs4
import collections
import logging
import re
import sys

from six import reraise

import gluetool
from gluetool import GlueError
from gluetool.log import log_dict
from gluetool.utils import fetch_url, PatternMap, IncompatibleOptionsError, render_template

import gluetool_modules
from gluetool_modules.libs.testing_environment import TestingEnvironment

# Type annotations
from typing import cast, Any, Callable, Dict, List, Optional, Tuple, Type, Union  # noqa


DEFAULT_NIGHTLY_LISTING = 'http://download.eng.brq.redhat.com/nightly/'  # type: str
DEFAULT_BU_LISTING = 'http://download-node-02.eng.bos.redhat.com/rel-eng/updates/'  # type: str
SEPARATOR = ';'


class GuessEnvironment(gluetool.Module):
    """
    "Guess" compose/arch/distro/image/product/wow relevancy distro.

    Goal of this module is to at least partialy answer question about the testing environment
    for a given artifact - deduce what composes, architectures and other properties are
    necessary to begin with. Following modules may change or extend these.

    User can choose from different possible methods of "guessing":

    * ``autodetect``: module will use artifact to deduce as many properties possible,
      using mapping files (``--{distro,image,product,wow-relevancy-distro}-pattern-map``) and instruction
      files (``--environment-map``).

    * ``force``: instead of autodetection, use specified properties. Use ``--environment``,
      ``--distro``, ``--image``, ``--product`` and ``--wow-relevancy-distro`` options to set actual values.

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

    .. note::

       Following information covers "testing environment"-focused work which should replace the current
       "guess image/distro" based on build target. This module would prepare a list of testing environments,
       and the actual image or distro would then be derived from the environment, not the build target - ``distros``
       method would try to provide Beaker distro as close to a given "compose" as possible, to fullfill the request
       represented by the testing environment constraint. Single Source Of Truth.

       We are not that far yet, the code is present for testing purposes, there is no user of ``testing_environments``
       shared function, but there will be and we'll be slowly moving toward that goal.

    .. note::
       For guessing we use destination tag with a fallback to build target where applicable. Destination tag provides
       the most relevant information, build target is kept for backward compatibility.

    **Compose map**

    Set by ``--compose-map`` option. After ``testing_environments`` summarizes for what arches it needs to set up
    testing environments, this map is consulted to fill in compose(s) for each environment. Instructions are checked for
    each environment, with ``ENVIRONMENT`` variable representing the one currently examined.

    .. code-block:: yaml

       - rule: >
           ENVIRONMENT.arch == 's390x'
           and PRIMARY_TASK.component != "foo"
         remove-environment: true
         add-note:
           level: warning
           text: >
             We're not testing on s390x.

       # For foo, only x86_64 is supported in RHEL6
       - rule: >
           BUILD_TARGET.match('foo-\\d+(?:\\.\\d+)?-rhel-6-candidate')
           and ENVIRONMENT.arch != 'x86_64'
         remove-environment: true

       # RHEL-8 Docker images - expected to be tested on both RHEL-8 *and* RHEL-7 guests.
       # Thanks to the pattern for RHEL-8 below, we'd get RHEL-8 by default, but we want to
       # add RHEL-7 (including ALT...) guests.
       - rule: >
           BUILD_TARGET.match(RHEL_8_0_0.containers.build_target.brew)

         # This results in ENVIRONMENT.compose being set to RHEL_8_0_0.compose, and two new environments
         # are added, for remaining composes, with their arch being ENVIRONMENT.arch.
         set-compose:
           - '{{ RHEL_8_0_0.compose }}'
           - '{{ RHEL_7.LatestReleased.beaker.distro }}, BUC'
           - '{{ RHEL_7_Alt.LatestReleased.beaker.distro }}, BUC'
    """

    name = 'guess-environment'
    # pylint: disable=line-too-long
    description = 'Guess testing environment properties (compose/arch/distro/image/wow relevancy env) for artifacts'

    options = [
        ('Methods', {
            'environment-method': {
                'help': 'What method to use for environment "guessing" (default: %(default)s).',
                'choices': ('autodetect', 'target-autodetection', 'force', 'nightly', 'buc'),
                'default': 'autodetect'

            },
            'distro-method': {
                'help': 'What method to use for distro "guessing" (default: %(default)s).',
                'choices': ('autodetect', 'target-autodetection', 'force', 'nightly', 'buc'),
                'default': 'autodetect'
            },
            'image-method': {
                'help': 'What method to use for image "guessing" (default: %(default)s).',
                'choices': ('autodetect', 'target-autodetection', 'force', 'recent'),
                'default': 'autodetect'
            },
            'product-method': {
                'help': 'What method to use for product "guessing" (default: %(default)s).',
                'choices': ('autodetect', 'target-autodetection', 'force'),
                'default': 'autodetect'
            },
            'wow-relevancy-distro-method': {
                'help': 'What method to use for wow relevancy distro "guessing" (default: %(default)s).',
                'choices': ('autodetect', 'target-autodetection', 'force'),
                'default': 'autodetect'
            }
        }),
        ('Specifications', {
            'environment': {
                'help': 'Testing environment to use. Accepted with ``--environment-method=force`` (default: none).',
                'action': 'append',
                'metavar': 'compose=...,arch=...,...',
                'default': []
            },
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
            },
            'wow-relevancy-distro': {
                'help': 'Wow relevancy distro identification, to help your method with guessing.'
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
            'arch-compatibility-map': {
                'help': """
                        Mapping between artifact arches and the actual arches we can use to test them (e.g. i686
                        can be tested on both x86_64 and i686 boxes (default: %(default)s).
                        """,
                'metavar': 'FILE',
                'default': None
            },
            'arch-completeness-map': {
                'help': """
                        Mapping between build target and a list of arches that represent *complete* set of arches
                        we can test for artifacts of such target (default: %(default)s).
                        """,
                'metavar': 'FILE',
                'default': None
            },
            'compose-map': {
                'help': 'Test and path to a file with instructions for environment(s) mapping (default: none).',
                'metavar': '(destination_tag|build_target):PATH',
                'action': 'append',
                'default': []
            },
            'distro-pattern-map': {
                'help': 'Test and path to a file with distro patterns (default: none).',
                'metavar': '(destination_tag|build_target):PATH',
                'action': 'append',
                'default': []
            },
            'image-pattern-map': {
                'help': 'Test and path to a file with image patterns (default: none).',
                'metavar': '(destination_tag|build_target):PATH',
                'action': 'append',
                'default': []
            },
            'product-pattern-map': {
                'help': 'Test and path to a file with product patterns (default: none).',
                'metavar': '(destination_tag|build_target):PATH',
                'action': 'append',
                'default': []
            },
            'wow-relevancy-distro-pattern-map': {
                'help': 'Test and path to a file with wow relevancy distro patterns (default: none).',
                'metavar': '(destination_tag|build_target):PATH',
                'action': 'append',
                'default': []
            }
        })
    ]

    shared_functions = ['testing_environments', 'distro', 'image', 'product', 'wow_relevancy_distro']

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    def __init__(self, *args, **kwargs):
        # type: (Any, Any) -> None
        super(GuessEnvironment, self).__init__(*args, **kwargs)

        self._testing_environments = {}  # type: Dict[str, List[TestingEnvironment]]
        self._distro = {}  # type: Dict[str, Union[str, List[str]]]
        self._image = {}  # type: Dict[str, Union[str, List[str]]]
        self._product = {}  # type: Dict[str, Union[str, List[str]]]
        self._wow_relevancy_distro = {}  # type: Dict[str, Union[str, List[str]]]

    def testing_environments(self):
        # type: () -> List[TestingEnvironment]
        """
        Return list of testing environments appropriate for the primary task.

        :rtype: list(TestingEnvironment)
        """

        if self._testing_environments['result'] is None:
            self.execute_method(self._testing_environments)

        return self._testing_environments['result']

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

    def wow_relevancy_distro(self, distro):
        # type: (Any) -> Union[str, List[str]]
        """
        Return guessed wow relevancy distro.
        Wow relevancy distro is a part of wow environment and is used for defining distro wow needs to test.
        For example, a user needs to run tests for an upcoming minor release. In this case we can't just pass
        `distro` to wow, because the `distro` describes a released version.

        :param distro: beaker distro with which the wow relevancy distro is related to
        :rtype: Union[str, List[str]]
        """
        if self._wow_relevancy_distro['result'] is None:
            self.execute_method(self._wow_relevancy_distro, distro)
        return self._wow_relevancy_distro['result']

    @gluetool.utils.cached_property
    def _arch_compatibility_map(self):
        # type: () -> Dict[str, List[str]]

        if not self.option('arch-compatibility-map'):
            return {}

        return cast(
            Dict[str, List[str]],
            gluetool.utils.load_yaml(self.option('arch-compatibility-map'), logger=self.logger)
        )

    @gluetool.utils.cached_property
    def _arch_completeness_map(self):
        # type: () -> Optional[PatternMap]

        if not self.option('arch-completeness-map'):
            return None

        return PatternMap(self.option('arch-completeness-map'), logger=self.logger)

    @gluetool.utils.cached_property
    def _compose_map(self):
        # type: () -> Any

        if not self.option('compose-map'):
            return []

        return gluetool.utils.load_yaml(gluetool.utils.normalize_path(self.option('compose-map')))

    def pattern_map(self, source, test):
        # type: (Dict[str, Union[str, List[str]]]) -> PatternMap
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

        pattern_map_path = source['pattern-map'].get(test, None)

        if not pattern_map_path:
            return None

        return PatternMap(pattern_map_path,
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

    def _guess_force(self, source):
        # type: (Dict[str, Union[str, List[str]]]) -> None
        if source['type'] == 'distro':
            source['result'] = [s.strip() for s in source['specification']]

        else:
            source['result'] = source['specification']

    def _guess_autodetect(self, source, test, tag, *args):
        # type: (Dict[str, Union[str, List[str]]], str) -> bool

        # wow relevancy distro is related not only to tag, but on beaker distro as well
        if source['type'] == 'wow_relevancy_distro':
            # wow relevancy distro is passed from the *args
            tag = SEPARATOR.join([tag, args[0]])

        try:
            pattern_map = self.pattern_map(source, test)

            if not pattern_map:
                self.warn("no map for test '{}'".format(test))
                return False

            source['result'] = pattern_map.match(tag, multiple=(source['type'] == 'distro'))
            return True

        except GlueError as exc:
            if exc.message.startswith('Could not match string'):
                return False

            # in case ther matching failed for some unexpected reason
            reraise(*sys.exc_info())

    def _guess_autodetect_environments(self, source):
        self.require_shared('evaluate_instructions', 'primary_task')

        # We start with arches available in the artifact, and with arches supported by the provisioner.
        # We match architectures present in the artifact with a list of architectures provisioner can provide,
        # and we find out what architectures we need (or cannot get...). And, by the way, whether there's
        # anything left to test.
        #
        # We need to account for architectures that are not supported but which may be compatible with a supported
        # architecture as well.

        source['result'] = []

        # These are arches which we'd use to constraint the schedule - we're going to add to this list later - ...
        constraint_arches = []  # type: List[str]

        provisioner_capabilities = self.shared('provisioner_capabilities')
        log_dict(self.debug, 'provisioner capabilities', provisioner_capabilities)

        # ... these are arches available in the artifact...
        artifact_arches = self.shared('primary_task').task_arches.arches
        log_dict(self.debug, 'artifact arches', artifact_arches)

        # ... these are *valid* artifact arches - those supported by the provisioner...
        valid_arches = []  # type: List[str]

        # ... and these are arches supported by the provisioner.
        supported_arches = provisioner_capabilities.available_arches if provisioner_capabilities else []
        log_dict(self.debug, 'supported arches', supported_arches)

        # When provisioner's so bold that it supports *any* architecture, give him every architecture present
        # in the artifact, and watch it burn :)
        #
        # Note that when the only artifact arch is `noarch`, it gets removed from constraints later, we have
        # an extra step dealing with `noarch`. Because obviously we can't get `noarch` guest from provisioner.
        if supported_arches is gluetool_modules.libs.ANY:
            valid_arches = artifact_arches
            constraint_arches = artifact_arches

        else:
            for arch in artifact_arches:
                # artifact arch is supported directly
                if arch in supported_arches:
                    valid_arches.append(arch)
                    constraint_arches.append(arch)
                    continue

                # It may be possible to find compatible architecture, e.g. it may be fine to test
                # i686 artifacts on x86_64 boxes. Let's check the configuration.

                # Start with a list of arches compatible with `arch`.
                compatible_arches = self._arch_compatibility_map.get(arch, [])

                # Find which of these are supported.
                compatible_and_supported_arches = [
                    compatible_arch for compatible_arch in compatible_arches if compatible_arch in supported_arches
                ]

                # If there are any compatible & supported, add the original arch to the list of valid arches,
                # because we *can* test it, but use the compatible arches for constraints - we cannot ask
                # provisioner (yet) to provide use the original arch, because it already explicitely said
                # "not supported". We can test artifacts of this archtiecture, but using other arches as
                # the environment.
                if compatible_and_supported_arches:
                    # Warning, because nothing else submits to Sentry, and Sentry because
                    # problem of secondary arches doesn't fit well with nice progress of
                    # testing environments, and I'd really like to observe the usage of
                    # this feature, without grepping all existing logs :/ If it's being
                    # used frequently, we can always silence the Sentry submission.

                    self.warn("Artifact arch '{}' not supported but compatible with '{}'".format(
                        arch, ', '.join(compatible_and_supported_arches)
                    ), sentry=True)

                    valid_arches.append(arch)
                    constraint_arches += compatible_and_supported_arches

        log_dict(self.debug, 'valid artifact arches', valid_arches)
        log_dict(self.debug, 'constraint arches', constraint_arches)

        if not valid_arches:
            # Here we would raise an exception, but since we're running in "just testing, nothing to see here"
            # mode, we're not going to interrupt the pipeline. Other modules will notice this problem for sure.

            self.debug('testing environments: no valid arches, no environments')
            return

            # raise NoTestableArtifactsError(self.shared('primary_task'), supported_arches)

        # `noarch` is supported naturally on all other arches, so, when we encounter an artifact with just
        # the `noarch`, we "reset" the list of constraints to let scheduler plugin know we'd like to get all
        # arches possible. But we have to be careful and take into account what provisioner told us about itself,
        # because we could mislead the scheduler plugin into thinking that every architecture is valid - if
        # provisioner doesn't support "ANY" arch, we have to prepare constraints just for the supported arches.
        # We can use all of them, true, because it's `noarch`, but we have to limit the testing to just them.
        if valid_arches == ['noarch']:
            self.debug("'noarch' is the only valid arch")

            # If provisioner boldly promised anything was possible, empty list of valid arches would result
            # into us not placing any constraints on the environments, and we should get really everything.
            #
            # And since we don't know what the list of all arches valid for this artifact looks like, we need
            # to take a peek into a configuration...
            if supported_arches is gluetool_modules.libs.ANY:
                if not self._arch_completeness_map:
                    # Again, we're just an observer, it's not up to us to break the pipeline - yet.
                    self.warn('Arch-completeness map not specified', sentry=True)

                    self.debug('testing environments: no arch-completeness map, no environments')
                    return

                primary_task = self.shared('primary_task')

                # primarly we use destination_tag for matching, with fallback to build target
                try:
                    constraint_arches = self._arch_completeness_map.match(primary_task.destination_tag, multiple=True)
                except GlueError:
                    constraint_arches = self._arch_completeness_map.match(primary_task.target, multiple=True)

            # On the other hand, if provisioner can support just a limited set of arches, don't be greedy.
            else:
                constraint_arches = supported_arches

        # When `noarch` is not the single valid arch, other arches dictate what constraints should we use.
        # Imagine an arch-specific "main" RPM, with noarch plugins - we cannot just throw in other supported
        # arches, because we'd not be able to test the "main" RPM, but thanks to "main" RPM, there should
        # be - and obviously are - other arches in the list, not just noarch. So, we do nothing, but, out
        # of curiosity, a warning would be nice to track this - it's a complicated topic, let's not get it
        # unnoticed, the assumption above might be completely wrong.
        elif 'noarch' in valid_arches:
            self.warn(
                "Artifact has 'noarch' bits side by side with regular bits ({})".format(', '.join(valid_arches)),
                sentry=True
            )

        log_dict(self.debug, 'constraint arches (noarch pruned)', constraint_arches)

        # Get rid of duplicities - when we found an unsupported arch, we added all its compatibles to the list.
        # This would lead to us limiting scheduler to provide arches A, B, C, C, ... and so on, because usualy
        # there's a primary arches A (supported), B (supported), D and E (both unsupported but compatible with C).
        # leading to C being present multiple times, replacing D and E.
        constraint_arches = list(set(constraint_arches))

        log_dict(self.debug, 'constraint arches (duplicities pruned)', constraint_arches)

        input_constraints = [
            TestingEnvironment(arch=arch, compose=None) for arch in constraint_arches
        ]

        output_constraints = input_constraints[:]

        # Helper function, add new environment & log it.
        def _add_new_environment(arch, compose, context):
            # type: (str, str, Dict[str, Any])

            environment = TestingEnvironment(arch=arch, compose=render_template(compose, **context))

            self.debug('adding environment: {}'.format(environment))

            output_constraints.append(environment)

        def _add_note(instruction, command, argument, context):
            if 'text' not in argument:
                raise GlueError('Note text is not set')

            self.shared('add_note', argument['text'], level=argument.get('level', logging.INFO))

        def _add_environment(instruction, command, argument, context):
            if isinstance(argument, dict):
                argument = [argument]

            if not isinstance(argument, list):
                raise GlueError("Cannot handle 'add-environment' argument of type {}".format(type(argument)))

            for environment in argument:
                if 'arch' not in environment or 'compose' not in environment:
                    raise GlueError('Environment must specify both arch and compose')

                _add_new_environment(environment['arch'], environment['compose'], context)

        def _remove_environment(instruction, command, argument, context):
            current_environment = context['ENVIRONMENT']

            self.debug('removing environment: {}'.format(current_environment))

            output_constraints.remove(current_environment)

        def _set_compose(instruction, command, argument, context):
            current_environment = context['ENVIRONMENT']

            if isinstance(argument, str):
                self.debug('updating environment: {}: compose={}'.format(current_environment, argument))

                current_environment.compose = render_template(argument, **context)

            elif isinstance(argument, list):
                first_compose = argument[0]

                self.debug('updating environment: {}: compose={}'.format(current_environment, first_compose))

                current_environment.compose = render_template(first_compose, **context)

                for compose in argument[1:]:
                    _add_new_environment(current_environment.arch, compose, context)

        for environment in input_constraints:
            context = gluetool.utils.dict_update(
                self.shared('eval_context'),
                {
                    'ENVIRONMENT': environment
                }
            )

            self.shared('evaluate_instructions', self._compose_map, {
                'add-note': _add_note,
                'add-environment': _add_environment,
                'remove-environment': _remove_environment,
                'set-compose': _set_compose
            }, context=context)

        log_dict(self.debug, 'testing environments', output_constraints)

        source['result'] = output_constraints

    def _guess_target_autodetect(self, source, *args):
        # type: (Dict[str, Union[str, List[str]]]) -> None
        self.require_shared('primary_task')

        if source['type'] == 'environment':
            self._guess_autodetect_environments(source)

        else:
            primary_task = self.shared('primary_task')

            # by default we match with destination_tag
            result = self._guess_autodetect(source, 'destination_tag', primary_task.destination_tag, *args)

            # we fallback to build target for legacy reasons
            if not result:
                result = self._guess_autodetect(source, 'build_target', primary_task.target, *args)

            # raise and error if no match
            if not result:
                raise GlueError("Failed to autodetect '{}', no match found".format(source['type']))

    _methods = {
        'autodetect': _guess_target_autodetect,
        'force': _guess_force,
        'target-autodetection': _guess_target_autodetect,
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

        def _parse_pattern_map(option):
            maps = {}

            for pattern_map_spec in gluetool.utils.normalize_multistring_option(self.option(option)):
                try:
                    test, path = pattern_map_spec.split(':', 1)

                except ValueError:
                    # Keep things backward compatible - if there's no test, assume build target.
                    test = 'build_target'
                    path = pattern_map_spec

                maps[test] = path

            return maps

        self._testing_environments = {
            'type': 'environment',
            'specification': self.option('environment'),
            'method': self.option('environment-method'),
            'pattern-map': _parse_pattern_map('compose-map'),
            'result': None
        }
        self._distro = {
            'type': 'distro',
            'specification': self.option('distro'),
            'method': self.option('distro-method'),
            'pattern-map': _parse_pattern_map('distro-pattern-map'),
            'result': None
        }
        self._image = {
            'type': 'image',
            'specification': self.option('image'),
            'method': self.option('image-method'),
            'pattern-map': _parse_pattern_map('image-pattern-map'),
            'result': None
        }
        self._product = {
            'type': 'product',
            'specification': self.option('product'),
            'method': self.option('product-method'),
            'pattern-map': _parse_pattern_map('product-pattern-map'),
            'result': None
        }
        self._wow_relevancy_distro = {
            'type': 'wow_relevancy_distro',
            'specification': self.option('wow-relevancy-distro'),
            'method': self.option('wow-relevancy-distro-method'),
            'pattern-map': _parse_pattern_map('wow-relevancy-distro-pattern-map'),
            'result': None
        }

    def sanity(self):
        # type: () -> None

        # Packs sources here, because self.option is unavailable in __init__
        self._pack_sources()

        specification_required = ('force', 'recent', 'nightly', 'buc')
        specification_ignored = ('autodetect', 'target-autodetection',)

        for source in [self._distro, self._image, self._product, self._wow_relevancy_distro]:

            if source['method'] == 'target-autodetection' and not source['pattern-map']:
                raise GlueError(
                    "--{}-pattern-map option is required with method '{}'".format(
                        source['type'], source['method']))

            if source['method'] in specification_required and source['specification'] is None:
                raise IncompatibleOptionsError(
                    "--{} option is required with method '{}'".format(source['type'], source['method']))

            if source['method'] in specification_ignored and source['specification'] not in [None, []]:
                raise IncompatibleOptionsError(
                    "--{} option is ignored with method '{}'".format(source['type'], source['method']))

    def execute_method(self, source, *args):
        # type: (Dict[str, Union[str, List[str]]]) -> None

        method = self._methods.get(source['method'], None)  # type: ignore
        if method is None:
            raise IncompatibleOptionsError("Unknown 'guessing' method '{}'".format(source['method']))

        method(self, source, *args)

        log_dict(self.info, 'Using {}'.format(source['type']), source['result'])

    def execute(self):
        # Nobody uses this information, but we want to examine it from time to time, in logs.
        # Also avoid running it every time the module is invoked - often this may happen in
        # a pipeline without any artifact, and the module would simply die because of missing
        # primary artifact.
        if self.has_shared('primary_task'):
            self.testing_environments()
