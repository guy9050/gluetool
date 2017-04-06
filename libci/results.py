import os

import libci


class TestResult(object):
    """
    This class represents results of testing performed by a module.

    Meaning of most of the fields depends on the result type, there are only
    few "points of contact" between two different result types.

    :param str test_type: Type of testing. Makes sense to producers and consumers,
      ``Result`` class does not care of its actual value.
    :param str overall_result: Overall result of the test, e.g. ``PASS``, ``FAIL`` or ``ERROR``.
      The actual value depends on producer's view of the testing process and its results.
    :param dict ids: producer may want to attach arbitrary IDs to the result, e.g.
      test run ID (default: empty)
    :param dict url: producer may want to attach arbitrary URLs to the result, e.g.
      address of 3rd party service website presenting the details of performed tests.
    :param payload: producer may want to attach arbitratry data to the result, e.g.
      list of individual tests and their results performed.

    :ivar str test_type: Type of the test.
    :ivar str overall_result: Overall result of the testing process, e.g. ``PASS``, ``FAIL``
      or ``ERROR``.
    :ivar dict ids: IDs producer think might interest the result consumer.
    :ivar dict urls: URLs producer think might interest the result consumer.
    :ivar payload: Data producer think might interest the result consumer.
    """

    # pylint: disable=too-many-arguments,too-few-public-methods

    def __init__(self, test_type, overall_result, ids=None, urls=None, payload=None):
        self.test_type = test_type
        self.overall_result = overall_result
        self.ids = ids or {}
        self.urls = urls or {}
        self.payload = payload or []

        if 'jenkins_build' not in self.urls:
            if 'BUILD_URL' in os.environ:
                self.urls['jenkins_build'] = os.environ['BUILD_URL']
            else:
                self.urls['jenkins_build'] = '<Jenkins job URL not available>'

    def serialize(self):
        """
        Return JSON representation of the result.
        """

        return {
            'test_type': self.test_type,
            'overall_result': self.overall_result,
            'ids': self.ids,
            'urls': self.urls,
            'payload': self.payload
        }

    def __repr__(self):
        return libci.utils.format_dict(self.serialize())


def publish_result(module, result_class, *args, **kwargs):
    """
    Helper function for publishing test results. It creates a result instance,
    and makes it available for other modules.

    Requires shared function named ``results`` that returns list of results
    gathered so far.

    :param libci.ci.Module module: Module publishing the result.
    :param Result result_class: Class of the result.
    :param tuple args: arguments passed to result class constructor.
    :param dict kwargs: keyword arguments passed to result class constructor.
    """

    if not module.has_shared('results'):
        module.warn("Cannot publish results, no 'results' shared function found")
        return

    result = result_class(*args, **kwargs)
    module.debug('result:\n{}'.format(libci.utils.format_dict(result.serialize())))

    module.shared('results').append(result)
