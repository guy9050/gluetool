import bs4

import libci
from libci import CIError, SoftCIError, Module
from libci.log import format_dict
from libci.utils import fetch_url, cached_property, PatternMap


DEFAULT_NIGHTLY_LISTING = 'http://download.eng.brq.redhat.com/nightly/'
DEFAULT_BU_LISTING = 'http://download-node-02.eng.bos.redhat.com/rel-eng/updates/'


class IncompatibleOptionsError(SoftCIError):
    SUBJECT = 'Incompatible options detected'
    BODY = """
Configuration of your component uses incompatible options for `guess-beaker-distro` module:

    {message}

Please, review the configuration of your component - the default settings are usually sane
and should not lead to this error. For valid options, their values and possible combinations
see documentation for `guess-beaker-distro` ([1]).

[1] https://url.corp.redhat.com/dbb9190
    """


class CIGuessBeakerDistro(Module):
    """
    "Guess" distro. User can choose from different possible methods of "guessing":

    * ``target-autodetection``: module will transform target of brew task to a distro,
      using provided regex patterns in ``--pattern-map`` file

    * ``force``: use specified distro no matter what. Use ``--distro`` option to set *what*
      distro you wish to use

    * ``nightly``: check the nightly composes, and choose the recent available. Use ``--distro``
      option to specify which distro you talk about (e.g. ``RHEL-7.4`` will check RHEL-7.4
      nightlies, and you'll get something like ``RHEL-7.4-20170223.n.0``.

    * ``buc``: check the batch update composes, and choose the recent available. Use ``--distro``
      option to specify which distro you talk about (e.g. ``RHEL-7.3`` will check RHEL-7.3
      composes, and you'll get something like ``RHEL-7.3-updates-20170405.0``.
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

    supported_dryrun_level = libci.ci.DryRunLevels.DRY

    _distro = None

    def distro(self):
        """ return guessed distro value """
        return self._distro

    @cached_property
    def pattern_map(self):
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

        return PatternMap(self.option('pattern-map'), spices={
            'BUC': _create_buc_repl,
            'NIGHTLY': _create_nightly_repl
        }, logger=self.logger)

    def _get_latest_finished_compose(self, base_url, hint):
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

        except CIError:
            raise CIError('Cannot get list of available composes at {}'.format(base_url))

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

        self.debug("Looking for latest valid BU compose for '{}'".format(hint))

        # First, try to take "latest-FOO" shortcut
        url = self.option('bu-listing') + '/latest-{}'.format(hint) + '/COMPOSE_ID'

        try:
            _, content = fetch_url(url, logger=self.logger)
            return content.strip()

        except CIError:
            self.warn("Cannot find shortcut '/latest-{}'".format(hint))

        # Ok, so there's no "/latest-<hint>" directory, lets iterate over all available composes
        # under "/<hint>"
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
        self.require_shared('primary_task')

        target = self.shared('primary_task').target

        self._distro = self.pattern_map.match(target)
        self.debug("transformed target '{}' to the distro '{}'".format(target, self._distro))

    def _guess_nightly(self):
        self._distro = self._find_nightly_for_distro(self.option('distro'))

    def _guess_buc(self):
        self._distro = self._find_buc_for_distro(self.option('distro'))

    _methods = {
        'force': _guess_force,
        'target-autodetection': _guess_target_autodetection,
        'nightly': _guess_nightly,
        'buc': _guess_buc
    }

    def sanity(self):
        distro_required = ('force', 'nightly', 'buc')
        distro_ignored = ('target-autodetection',)

        method = self.option('method')
        distro = self.option('distro')

        if method == 'target-autodetection' and not self.option('pattern-map'):
            raise CIError("--pattern-map option is required with method '{}'".format(method))

        if method in distro_required and not distro:
            raise IncompatibleOptionsError("--distro option is required with method '{}'".format(method))

        if method in distro_ignored and distro:
            raise IncompatibleOptionsError("--distro option is ignored with method '{}'".format(method))

    def execute(self):
        method = self._methods.get(self.option('method'), None)
        if method is None:
            raise IncompatibleOptionsError("Unknown 'guessing' method '{}'".format(self.option('method')))

        method(self)
        self.info("Using distro '{}'".format(self._distro))
