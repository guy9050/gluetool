import re
import urllib2
import bs4
from libci import CIError, Module
from libci.utils import log_blob, format_dict


DEFAULT_NIGHTLY_LISTING = 'http://download.eng.brq.redhat.com/nightly/'


class CIGuessBeakerDistro(Module):
    """
    "Guess" distro. User can choose from different possible methods of "guessing":

      - 'target-autodetection': module will transform target of brew task to a distro,
      e.g. 'rhel-7.3-candidate' => 'rhel-7.3'. This is the default method.

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
        }
    }

    shared_functions = ['distro']

    _distro = None

    def distro(self):
        """ return guessed distro value """
        return self._distro

    def _guess_force(self):
        distro = self.option('distro')
        self.debug("forcing '{}' as a distro".format(distro))

        self._distro = distro

    def _guess_target_autodetection(self):
        task = self.shared('brew_task')
        if task is None:
            raise CIError("Using 'target-autodetect' method without a brew task does not work")

        translations = {
            # default translation for rhel and staging branches
            r'(staging-|supp-)?(rhel-[0-9]+)(.[0-9]+)?-?(z)?-candidate':
                lambda match: '{}{}{}'.format(match.group(2),
                                              match.group(3) or '',
                                              '.' + match.group(4) if match.group(4) else ''),
            # RHEL LE 7.1 product
            r'rhel-7.1-ppc64le-?(z)?-candidate':
                lambda match: 'rhel-le-7.1{}'.format('.' + match.group(1) if match.group(1) else '')
        }

        for regex, function in translations.items():
            match = re.match(regex, task.target.target)
            if match:
                self._distro = function(match)
                break
        else:
            raise CIError("could not translate build target '{}' to distro".format(task.target.target))

        self.debug("transformed target '{}' to distro '{}'".format(task.target.target, self._distro))

    def _guess_nightly(self):
        distro = self.option('distro')
        url = self.option('nightly-listing')

        self.debug("Fetching list of nightly composes from '{}'".format(url))

        response = urllib2.urlopen(url)
        code, content = response.getcode(), response.read()

        log_blob(self.debug, '{}: {}'.format(url, code), content)

        if code != 200:
            raise CIError('Cannot get list of nightly composes')

        soup = bs4.BeautifulSoup(content, 'html.parser')

        nightlies = [(link.string.replace('/', ''), link['href'])
                     for link in soup.find_all('a') if link.string.startswith(distro)]

        self.debug('available nightlies:\n{}'.format(format_dict(nightlies)))

        for name, href in sorted(nightlies, key=lambda x: x[0], reverse=True):
            self.debug("Checking status of '{}'".format(name))

            status_url = url + '/' + href + '/STATUS'
            response = urllib2.urlopen(status_url)
            code, content = response.getcode(), response.read()

            log_blob(self.debug, '{}: {}'.format(status_url, code), content)

            if content.strip() != 'FINISHED':
                self.debug('{} not marked as finished'.format(name))
                continue

            self._distro = name
            break

        else:
            raise CIError('None of examined nightlies was marked as FINISHED')

    _methods = {
        'force': _guess_force,
        'target-autodetection': _guess_target_autodetection,
        'nightly': _guess_nightly
    }

    def sanity(self):
        distro_required = ('force', 'nightly')
        distro_ignored = ('target-autodetection')

        method = self.option('method')
        distro = self.option('distro')

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
