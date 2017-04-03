import libci


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
        pass

    def destroy(self, failure=None):
        with open(self.option('results-file'), 'w') as f:
            f.write(libci.utils.format_dict([result.serialize() for result in self._results]))
            f.flush()

        self.info("Results saved into '{}'".format(self.option('results-file')))
