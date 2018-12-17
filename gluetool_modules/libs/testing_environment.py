import collections

import gluetool
from gluetool.utils import normalize_multistring_option

import gluetool_modules.libs


BaseTestingEnvironment = collections.namedtuple('TestingEnvironment', [
    'compose',
    'arch'
])


class TestingEnvironment(BaseTestingEnvironment):
    """
    To specify what environment should provisioner provide when asked for guest(s), one needs to
    describe attributes of such environment. It's up to provisioning modules to decode the information,
    and provision guest that would - according to their best knowledge - satisfy the request.

    Follows :doc:`Testing Environment Protocol </protocols/testing-environment>`.

    .. note::

       This is effectively a work in progress - we need to separate environments from provisioners
       and test runners, and that would let us make modules less dependant on the implementation
       of guests.

    :param str compose: Identification of the compose to be used for testing. It can be pretty much
        any string value, its purpose is to allow provisioning modules to chose the best distro/image/etc.
        suitable for the job. It will depend on what modules are connected in the pipeline, how they are
        configured and other factors. E.g. when dealing with ``workflow-tomorrow``, it can carry a tree
        name as known to Beaker, ``RHEL-7.5-updates-20180724.1`` or ``RHEL-6.10``; the provisioner should
        then deduce what guest configuration (arch & distro, arch & OpenStack image, and so on) would satisfy
        such request.
    :param str arch: Architecture that should be used for testing.
    """

    # Make special values available to templates, they are now reachable as class variables
    # of each instance.
    ANY = gluetool_modules.libs.ANY

    def __str__(self):
        return self.serialize_to_string()

    def serialize_to_string(self):
        # type: () -> str
        """
        Serialize testing environment to comma-separated list of keys and their values, representing
        the environment.

        :rtype: str
        :returns: testing environemnt properties in ``key1=value1,...`` form.
        """

        return ','.join([
            '{}={}'.format(field, getattr(self, field)) for field in sorted(self._fields)
        ])

    def serialize_to_json(self):
        # type: () -> Dict[str, Any]
        """
        Serialize testing environment to a JSON dictionary.

        :rtype: dict(str, object)
        """

        return {
            field: getattr(self, field) for field in sorted(self._fields)
        }

    @classmethod
    def _assert_env_properties(cls, env_properties):
        for env_property in env_properties:
            if env_property in cls._fields:
                continue

            raise gluetool.GlueError("Testing environment does not have property '{}'".format(env_property))

    @classmethod
    def unserialize_from_string(cls, serialized):
        # type: (str) -> TestingEnvironment
        """
        Construct a testing environment from a comma-separated list of key and their values.

        :param str serialized: testing environment properties in ``key1=value1,...`` form.
        :rtype: TestingEnvironment
        """

        normalized = normalize_multistring_option(serialized)

        env_properties = {
            key.strip(): value.strip() for key, value in [
                env_property.split('=') for env_property in normalized
            ]
        }

        cls._assert_env_properties(env_properties.keys())

        return TestingEnvironment(**env_properties)

    @classmethod
    def unserialize_from_json(cls, serialized):
        # type: (Dict[str, Any]) -> TestingEnvironment
        """
        Construct a testing environment from a JSON representation of fields and their values.

        :param dict(str, object) serialized: testing environment properties in a dictionary.
        :rtype: TestingEnvironment
        """

        cls._assert_env_properties(serialized.keys())

        return TestingEnvironment(**serialized)