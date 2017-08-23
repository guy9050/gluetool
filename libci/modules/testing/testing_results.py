import json
import libci

from libci.results import TestResult


DEFAULT_RESULTS_FILE = 'results.json'


class TestingResults(libci.Module):
    """
    Provides support for gathering and exporting testing results.

    Keeps internal ``list`` of produced results (instances of :py:class:`libci.results.TestResult`),
    and provides it to callers via its shared function :py:meth:`results`. Users can then modify the
    list and results it carries.

    The module is able to store results in the file (in JSON format), or initialize its internal
    store from provided file.

    :ivar list _results': Internal storage of results.
    """

    name = 'testing-results'

    supported_dryrun_level = libci.ci.DryRunLevels.DRY

    options = {
        'results-file': {
            'help': 'path to a file to store results into (default: {})'.format(DEFAULT_RESULTS_FILE),
            'default': DEFAULT_RESULTS_FILE
        },
        'init-file': {
            'help': 'Initialize results from given file',
        }
    }

    shared_functions = ('results',)

    def __init__(self, *args, **kwargs):
        super(TestingResults, self).__init__(*args, **kwargs)

        self._results = []

    def results(self):
        """
        Return list of gathered results.

        :rtype: list
        :returns: list of gathered results (instances of :py:class:`libci.results.TestResult`).
        """

        return self._results

    def execute(self):
        initfile = self.option('init-file')

        if initfile is None:
            return

        # load results from init file
        try:
            with open(initfile, 'r') as f:
                for result in json.load(f):
                    self._results.append(TestResult(test_type=result['test_type'],
                                                    overall_result=result['overall_result'],
                                                    ids=result['ids'],
                                                    urls=result['urls'],
                                                    payload=result['payload']))
        except KeyError as e:
            raise libci.CIError('init file is invalid, key {} not found'.format(e))
        except IOError as e:
            raise libci.CIError(e)

    def destroy(self, failure=None):
        if not self.dryrun_allows('Exporting results into a file'):
            return

        # the results-file option can be empty if parsing of arguments failed
        if self.option('results-file') is None:
            return

        with open(self.option('results-file'), 'w') as f:
            f.write(libci.utils.format_dict([result.serialize() for result in self._results]))
            f.flush()

        self.info("Results saved into '{}'".format(self.option('results-file')))
