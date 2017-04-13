import json
import libci

from libci.results import TestResult


DEFAULT_RESULTS_FILE = 'results.json'


class TestingResults(libci.Module):
    """
    Provides support for gathering and exporting testing results.
    """

    name = 'testing-results'

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

    _results = []

    def results(self):
        """
        Returns list of gathered results.
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
        with open(self.option('results-file'), 'w') as f:
            f.write(libci.utils.format_dict([result.serialize() for result in self._results]))
            f.flush()

        self.info("Results saved into '{}'".format(self.option('results-file')))
