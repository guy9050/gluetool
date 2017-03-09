import os.path
import re
import bs4
import yaml
from libci import CIError, Module
from libci.utils import format_dict, fetch_url, cached_property


DEFAULT_NIGHTLY_LISTING = 'http://download.eng.brq.redhat.com/nightly/'
DEFAULT_BU_LISTING = 'http://download-node-02.eng.bos.redhat.com/rel-eng/updates/'


class CIGuessBeakerDistro(Module):
    """
    "Guess" distro. User can choose from different possible methods of "guessing":

      - 'target-autodetection': module will transform target of brew task to a distro:
        - for z-candidate targets ('rhel-7.3-candidate') will try to find corresponding
        batch update compose ('RHEL-7.3-updates-20170222.0');
        - for non-z-candidate targets ('rhel-7.3-candidate') will transfor taget into
        a distro ('rhel-7.3')

      - 'force': use specified distro no matter what. Use --distro option to set *what*
      distro you wish to use

      - 'nightly': check the nightly composes, and choose the recent available. Use --distro
      option to specify which distro you talk about (e.g. 'RHEL-7.4' will check RHEL-7.4
      nightlies, and you'll get something like RHEL-7.4-20170223.n.0
    """

    name = 'guess-beaker-distro'
    description = 'Guess beaker distro from build target of a brew build'

    options = {
        'method': {
            'help': 'What method to use for distro "guessing"',
            'default': 'target-autodetection'
        },
        'distro': {
            'help': 'Distro specification, to help your method with guessing'
        },
        'nightly-listing': {
            'help': """URL where list of nightly composes lies, in a form of web server's
directory listing. Default is {}""".format(DEFAULT_NIGHTLY_LISTING),
            'default': DEFAULT_NIGHTLY_LISTING
        },
        'bu-listing': {
            'help': """URL where list of batch update composes lies, in a form of web server's
directory listing. Default is {}""".format(DEFAULT_BU_LISTING),
            'default': DEFAULT_BU_LISTING
        },
        'pattern-map': {
            'help': 'Path to a file with target => distro patterns.'
        }
    }

    shared_functions = ['distro']

    _distro = None

    def distro(self):
        """ return guessed distro value """
        return self._distro

    @cached_property
    def pattern_map(self):
        """
        Pattern map is a list of pattern: transform pairs. Pattern is a regex pattern
        used to match the build target, transform is either a string with backreferences,
        describing how to use groups matched by the patter to construct a distro name, or
        it's a string of multiple items, separated by comma - in that case, the first
        item is a string used as already described, and the second item names a function
        that should be applied to the result of that simple replacement.

        This is transformed into a list (to keep the order) of tuples (pattern, transform).
        Pattern is compiled regex pattern. If it matches the build target, transform is
        called, with pattern and build target as arguments. It is expected to return
        distro name.
        """

        path = os.path.expanduser(self.option('pattern-map'))

        if not os.path.exists(path):
            raise CIError("pattern map '{}' does not exist".format(path))

        with open(path, 'r') as f:
            pattern_map = yaml.load(f)

        if pattern_map is None:
            raise CIError("pattern map '{}' does not contain any patterns".format(path))

        self.debug('pattern-map:\n{}'.format(format_dict(pattern_map)))

        def _create_simple_repl(repl):
            def _replace(pattern, target):
                """
                Use `repl` to construct distro from `target`, honoring all backreferences made by `pattern`.
                """

                self.debug("pattern '{}', repl '{}', target '{}'".format(pattern.pattern, repl, target))

                try:
                    return pattern.sub(repl, target)

                except re.error as e:
                    raise CIError("Cannot transform pattern '{}' with target '{}', repl '{}': {}".format(
                        pattern.pattern, target, repl, str(e)))

            return _replace

        def _create_buc_repl(hint_repl):
            def _replace(pattern, target):
                """
                Use `hint_repl` function - which was created by `_create_simple_repl` - to get
                a hint which is then used to find out the batch update compose.
                """

                hint = hint_repl(pattern, target)
                self.debug("hint is '{}'".format(hint))

                return self._find_buc_for_distro(hint)

            return _replace

        def _create_nightly_repl(hint_repl):
            def _replace(pattern, target):
                """
                Use `hint_repl` function - which was created by `_create_simple_repl` - to get
                a hint which is then used to find out the nightly compose.
                """

                hint = hint_repl(pattern, target)
                self.debug("hint is '{}'".format(hint))

                return self._find_nightly_for_distro(hint)

            return _replace

        transform_spice = {
            'BUC': _create_buc_repl,
            'NIGHTLY': _create_nightly_repl
        }

        compiled_map = []

        for pattern_dict in pattern_map:
            pattern = pattern_dict.keys()[0]
            transform = [s.strip() for s in pattern_dict[pattern].split(',')]

            # first item in `transform` is always a "repl" for `pattern.sub()` call
            replace = _create_simple_repl(transform[0])

            if len(transform) > 1:
                # second item is another function that's using our "replace" function
                # for its own purposes, "spicing up" the transformation process

                spice = transform_spice.get(transform[1], None)
                if spice is None:
                    raise CIError("Unknown transform function '{}'".format(transform[1]))

                replace = spice(replace)

            compiled_map.append((re.compile(pattern), replace))

        return compiled_map

    def _get_latest_finished_compose(self, base_url, hint):
        """
        Fetch index page listing several composes from BASE_URL, and try to find
        the most recent and FINISHED one, using HINT to limit set of examined
        composes - if the composes starts with HINT, we'll check it.

        :param str base_url: URL of the index page. It should be a directory listing,
          with links leading to relevant composes.
        :param str hint: what composs should be examined - it the name of compose
          starts with `hint`, it's one of ours.
        :returns: name of the compose, or `None`.
        """

        # Fetch the index
        try:
            _, content = fetch_url(base_url, logger=self.logger)

        except CIError:
            raise CIError('Cannot get list of available composes')

        # Find all <a/> elements from the index
        soup = bs4.BeautifulSoup(content, 'html.parser')

        # [(text, href), ...]
        composes = [(link.string.replace('/', ''), link['href'])
                    for link in soup.find_all('a') if link.string.startswith(hint)]

        self.debug('available composes:\n{}'.format(format_dict(composes)))

        for name, href in sorted(composes, key=lambda x: x[0], reverse=True):
            self.debug("checking status of '{}'".format(name))

            # Check compose status
            url = '{}/{}/STATUS'.format(base_url, href)

            try:
                _, content = fetch_url(url, logger=self.logger)

            except CIError:
                self.warn("Cannot find out status of '{}'".format(name))
                continue

            if content.strip() != 'FINISHED':
                self.debug("'{}' not marked as finished".format(name))
                continue

            # Get its ID
            url = '{}/{}/COMPOSE_ID'.format(base_url, href)

            try:
                _, content = fetch_url(url, logger=self.logger)

            except CIError:
                self.warn("Cannot find out ID of '{}'".format(name))
                continue

            return content.strip()

        return None

    def _find_buc_for_distro(self, hint):
        """
        Find batch update compose for a given distro.

        :param str hint: Values like "RHEL-7.3", "RHEL-6.8", etc.
        :returns: BU compose name.
        """

        hint = hint.upper()

        self.debug("Looking for latest valid BU compose for '{}'".format(hint))

        # First, try to take "latest-FOO" shortcut
        url = self.option('bu-listing') + '/latest-{}'.format(hint) + '/COMPOSE_ID'

        try:
            _, content = fetch_url(url, logger=self.logger)
            return content.strip()

        except CIError:
            self.warn("Cannot find shortcut '/latest-{}'".format(hint))

        # Ok, so there's no "latest-FOO" directory, lets iterate over all available composes
        distro = self._get_latest_finished_compose('{}/{}'.format(self.option('bu-listing'), hint), hint)

        if distro is None:
            raise CIError('None of examined BU composes was acceptable')

        return distro

    def _find_nightly_for_distro(self, hint):
        """
        Find nightly compose for a give distro.

        :param str hint: Values like "RHEL-7.3", "RHEL-6.8", etc.
        :returns: Nightly compose name.
        """

        hint = hint.upper()

        self.debug("Looking for latest valid nightly compose for '{}'".format(hint))

        distro = self._get_latest_finished_compose(self.option('nightly-listing'), hint)

        if distro is None:
            raise CIError('None of examined nightly composes was acceptable')

        return distro

    def _guess_force(self):
        distro = self.option('distro')
        self.debug("forcing '{}' as a distro".format(distro))

        self._distro = distro

    def _guess_target_autodetection(self):
        task = self.shared('brew_task')
        if task is None:
            raise CIError("Using 'target-autodetect' method without a brew task does not work")

        target = task.target.target

        self.debug("trying to match target '{}'".format(target))

        for pattern, transform in self.pattern_map:
            self.debug("testing pattern '{}'".format(pattern.pattern))

            match = pattern.match(target)
            if match is None:
                continue

            self.debug('  matched')

            self._distro = transform(pattern, target)
            break

        else:
            raise CIError("could not translate build target '{}' to distro".format(target))

    def _guess_nightly(self):
        self._distro = self._find_nightly_for_distro(self.option('distro'))

    _methods = {
        'force': _guess_force,
        'target-autodetection': _guess_target_autodetection,
        'nightly': _guess_nightly
    }

    def sanity(self):
        distro_required = ('force', 'nightly')
        distro_ignored = ('target-autodetection',)

        method = self.option('method')
        distro = self.option('distro')

        if method == 'target-autodetection' and not self.option('pattern-map'):
            raise CIError("--pattern-map option is required with method '{}'".format(method), soft=True)

        if method in distro_required and not distro:
            raise CIError("--distro option is required with method '{}'".format(method), soft=True)

        if method in distro_ignored and distro:
            raise CIError("--distro option is ignored with method '{}'".format(method), soft=True)

    def execute(self):
        method = self._methods.get(self.option('method'), None)
        if method is None:
            raise CIError("Unknown 'guessing' method '{}'".format(self.option('method')), soft=True)

        method(self)
        self.info("Using distro '{}'".format(self._distro))
