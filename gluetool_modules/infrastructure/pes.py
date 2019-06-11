from urlparse import urljoin
import simplejson.errors  # type: ignore  # no stubfile for simplejson

from requests.exceptions import ConnectionError, HTTPError, Timeout

import gluetool
from gluetool.utils import cached_property
from gluetool.log import log_dict, LoggingFunctionType

from gluetool.utils import requests

# pylint: disable=no-name-in-module
from jq import jq

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import cast, Any, Dict, List, Optional, Tuple, Union  # noqa


DEFAULT_RETRY_TIMEOUT = 30
DEFAULT_RETRY_TICK = 10


class PESApi(object):
    # pylint: disable=too-few-public-methods
    """
    API to Package Evolution Service
    """

    def __init__(self, module):
        # type: (gluetool.Module) -> None

        self.api_url = module.option('api-url')  # type: str
        self.module = module  # type: gluetool.Module
        self.logger = module.logger  # type: gluetool.log.ContextAdapter

        # required for type checking, see gluetool.log for more details
        self.debug = cast(LoggingFunctionType, lambda s: None)

        self.logger.connect(self)

    def _post_payload(self, location, payload):
        # type: (str, Dict) -> Any
        url = urljoin(self.api_url, location)

        self.debug('[PES API]: {}'.format(url))

        def _post_response():
            # type: () -> Any
            try:
                with requests() as req:
                    response = req.post(url, json=payload)

                # 404 is expected if no events were found for a component
                if response.status_code not in [200, 404]:
                    raise gluetool.GlueError("Post '{}' to '{}' returned {}: {}".format(payload,
                                                                                        url,
                                                                                        response.status_code,
                                                                                        response.content))

                # show nice parsed output
                try:
                    log_dict(self.debug,
                             "[PES API] returned '{}' and following output".format(response.status_code),
                             response.json())

                # in case json decoding fails for the reponse, something is really wrong (e.g. wrong api-url)
                except simplejson.errors.JSONDecodeError:
                    raise gluetool.GlueError("Pes returned unexpected non-json output, needs investigation")

                # IMPORTANT: as 404 evaluates to False, so we need to force True
                if response.status_code == 404:
                    return (True, response)

                # let reponse evaluate to boolean for wait as designed
                return (response, response)

            except (ConnectionError, HTTPError, Timeout) as error:
                self.debug('retrying because of exception: {}'.format(error))

                # does not matter that response is not returned here, as it is ignored anyway
                return (False, error)

        # wait until we get a valid response
        (success, response) = gluetool.utils.wait('getting post response from {}'.format(url),
                                                  _post_response,
                                                  timeout=self.module.option('retry-timeout'),
                                                  tick=self.module.option('retry-tick'))

        # handle connection errors, when response is actually false
        if not success:
            raise gluetool.GlueError("Failed to get valid API response from PES: '{}'".format(response))

        return response

    def get_ancestors(self, package):
        # type: (str) -> List[str]
        """
        Get ancestors of the given package by querying Package Evolution Service. This can used
        for testing upgrades from the ancestor package(s) to the given package.

        :returns: List of ancestors of the package.
        """

        # Note: srpm-events endpoint MUST end with /
        response = self._post_payload('srpm-events/', {'name': package})

        # PES returns 'not found' if package has no events, which can mean actually two things:
        #
        # 1. package was present in previous release
        # 2. it is a new package, previously not present in previous release
        #
        # The case 2. needs to be handled later in the pipeline, i.e. not existing ancestor build
        if response.status_code == 404:
            return [package]

        # we are interested in srpm nams only and exclude present packages (as advised by the leapp team)
        query = '.[] | select(.action!=0) | .in_packageset.srpm | .[]'

        ancestors = jq(query).transform(response.json(), multiple_output=True)

        # remove duplicate ancestors and sort them, so their list is predictable
        return sorted(list(set(ancestors)))


class PES(gluetool.Module):
    """
    Provides API to Package Evolution Service via `pes_api` shared function.
    Provides function to find ancestors for a given package from previous major relases. Used for upgrades testing.
    """
    name = 'pes'
    description = 'Provides API to Package Evolution Service (PES)'

    options = [
        ('General options', {
            'api-url': {
                'help': 'PES API server URL',
                'type': str
            },
        }),
        ('Query options', {
            'retry-timeout': {
                'help': 'Wait timeout in seconds. (default: %(default)s)',
                'type': int,
                'default': DEFAULT_RETRY_TIMEOUT
            },
            'retry-tick': {
                'help': 'Number of times to retry the query. (default: %(default)s)',
                'type': int,
                'default': DEFAULT_RETRY_TICK
            },
        }),
        ('Testing options', {
            'map-primary-task': {
                'help': 'Finds ancestors for the component of the primary task',
                'action': 'store_true'
            }
        })
    ]

    required_options = ('api-url',)

    shared_functions = ['ancestors', 'pes_api']

    def __init__(self, *args, **kwargs):
        # type: (Any, Any) -> None

        super(PES, self).__init__(*args, **kwargs)

        self._components = []  # type: List[str]

    @cached_property
    def _pes_api(self):
        # type: () -> PESApi
        return PESApi(self)

    def pes_api(self):
        # type: () -> PESApi
        """
        Returns PESApi instance.
        """
        return cast(PESApi, self._pes_api)

    def ancestors(self, package):
        # type: (str) -> List[str]
        """
        Returns list of package ancestors from a previous major release.

        Note that this currently expects PES only holds ancestors for on previous major release.

        :param str package: Package to find ancestors for.
        """
        ancestors = self._pes_api.get_ancestors(package)

        self.info("Ancestors of '{}': {}".format(package, ', '.join(ancestors)))

        return cast(List[str], ancestors)

    def execute(self):
        # type: () -> None

        if self.option('map-primary-task'):

            self.require_shared('primary_task')

            try:
                component = self.shared('primary_task').component
            except AttributeError:
                raise gluetool.GlueError('No build available, cannot continue')

            self.ancestors(component)