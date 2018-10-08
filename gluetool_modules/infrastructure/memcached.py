from pymemcache.client import base

import gluetool


class Cache(object):
    def __init__(self, module, client):
        self._module = module
        self._client = client

    def get(self, key, default=None):
        """
        Return a value of key ``key`` or return value of ``default`` if the key does not exist.
        """

        return self._client.get(key, default=default)

    def gets(self, key, default=None, cas_default=None):
        """
        Return a tuple consiting of value of key ``key`` and CAS tag, or tuple of ``default`` and ``cas_default``
        if the key does not exist.
        """

        return self._client.gets(key, default=default, cas_default=cas_default)

    def set(self, key, value):
        """
        Set a value of key ``key`` to ``value``
        """

        return self._client.set(key, value)

    def cas(self, key, value, tag):
        """
        *Check And Set* operation. Set a value of key ``key`` to ``value``. ``tag`` is the *CAS tag* previously
        obtained by calling ``gets``. If the key was modified by other process/thread between ``gets`` and ``cas``,
        the update **is not** performed and the method returns ``False``. In such case, to successfully change the
        value, one must call ``gets`` again to obtain changed value and CAS tag, and pass new CAS tag to ``cas``
        method.

        If the value was not changed between ``gets`` and ``cas`` calls, ``cas`` return ``True``.
        """

        return self._client.cas(key, value, tag)


class Memcached(gluetool.Module):
    """
    Provides access to Memcached server.
    """

    name = 'memcached'
    description = 'Provides access to Memcached server.'

    options = {
        'server-hostname': {
            'help': 'Memcached server hostname.',
            'type': str
        },
        'server-port': {
            'help': 'Memcached server port.',
            'type': int
        }
    }

    required_options = ('server-hostname', 'server-port')

    shared_functions = ('cache',)

    @gluetool.utils.cached_property
    def _client(self):
        return base.Client((self.option('server-hostname'), self.option('server-port')))

    @gluetool.utils.cached_property
    def _cache(self):
        return Cache(self, self._client)

    def cache(self):
        """
        Returns an object providing access to the cache.

        Follows :doc:`Testing Environment Protocol </protocols/cache>`.
        """

        return self._cache
