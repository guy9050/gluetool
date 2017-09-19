import json
import sys

import libci
from libci.results import TestResult
from libci.utils import new_xml_element


class TestingResults(libci.Module):
    """
    Provides support for gathering and exporting testing results.

    Keeps internal ``list`` of produced results (instances of :py:class:`libci.results.TestResult`),
    and provides it to callers via its shared function :py:meth:`results`. Users can then modify the
    list and results it carries.

    The module is able to store results in a file, or initialize the internal list from a file.
    Different formats are supported, namely JSON (``json`` format) and xUnit (``xunit`` format).
    """

    name = 'testing-results'

    supported_dryrun_level = libci.ci.DryRunLevels.DRY

    options = {
        'results-file': {
            'help': 'Format and path to a file to store results into.',
            'metavar': 'FORMAT:PATH',
            'action': 'append',
            'default': []
        },
        'init-file': {
            'help': 'Format and path to initialize results from.',
            'metavar': 'FORMAT:PATH'
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

    def _parse_formats(self, option):
        # pylint: disable=no-self-use

        """
        Converts different forms on format:file specifications into a ``list``. These:

        * from a config file: ``format1:file1.ext, format2  :  file2.ext  ``
        * from an option (specified possibly multiple times): ``['format1:file1.ext', '  format2  : file2.ext  ']``

        will result into ``[('foo', 'bar'), ('bar', 'baz')]``.
        """

        specs = self.option(option)

        if isinstance(specs, str):
            specs = [s.strip() for s in specs.split(',')]

        parsed = []

        for spec in specs:
            if ':' not in spec:
                raise libci.CIError("Value '{}' of option '{}' does not specify format and filename".format(spec,
                                                                                                            option))

            parsed.append(tuple([s.strip() for s in spec.split(':')]))

        return parsed

    def _serialize_to_json(self, stream):
        stream.write(libci.utils.format_dict([result.serialize('json') for result in self._results]))

    def _serialize_to_xunit(self, stream):
        test_suites = new_xml_element('testsuites')

        for result in self._results:
            test_suites.append(result.serialize('xunit'))

        stream.write(test_suites.prettify(encoding='utf-8'))

    serializers = {
        'json': _serialize_to_json,
        'xunit': _serialize_to_xunit
    }

    def execute(self):
        initfile = self.option('init-file')

        if initfile is None:
            return

        input_format, input_file = self._parse_formats('init-file')[0]

        self.info("loading results from '{}', in format '{}'".format(input_file, input_format))

        def _default_unserialize(result):
            return TestResult.unserialize(self.ci, 'json', result)

        # load results from init file
        try:
            with open(input_file, 'r') as f:
                if input_format == 'json':
                    try:
                        results = json.load(f)

                    except ValueError as exc:
                        raise libci.CIError("Cannot load JSON data from file '{}': {}".format(input_file, str(exc)))

                    for result in results:
                        if 'result_class' in result:
                            klass_path = result['result_class'].split('.')
                            module_name, klass_name = '.'.join(klass_path[0:-1]), klass_path[-1]

                            if module_name not in sys.modules:
                                self.warn("Cannot find result module '{}'".format(module_name), sentry=True)
                                result = _default_unserialize(result)

                            elif not hasattr(sys.modules[module_name], klass_name):
                                self.warn("Cannot find result class '{}'".format(klass_name), sentry=True)
                                result = _default_unserialize(result)

                            else:
                                klass = getattr(sys.modules[module_name], klass_name)
                                result = klass.unserialize(self.ci, 'json', result)

                        else:
                            result = _default_unserialize(result)

                else:
                    raise libci.CIError("Input format '{}' is not supported".format(input_format))

                libci.log.log_dict(self.debug, 'result', result.serialize('json'))
                self._results.append(result)

        except KeyError as e:
            raise libci.CIError('init file is invalid, key {} not found'.format(e))
        except IOError as e:
            raise libci.CIError(e)

    def destroy(self, failure=None):
        # the results-file option can be empty if parsing of arguments failed
        if not self.option('results-file'):
            self.warn('No results file set.', sentry=True)
            return

        outputs = self._parse_formats('results-file')

        if not self.dryrun_allows('Exporting results into a file'):
            return

        libci.log.log_dict(self.debug, 'outputs', outputs)

        for output_format, output_file in outputs:
            serializer = self.serializers.get(output_format, None)

            if serializer is None:
                raise libci.CIError("Output format '{}' is not supported".format(output_format))

            with open(output_file, 'w') as f:
                serializer(self, f)
                f.flush()

            self.info("Results in format '{}' saved into '{}'".format(output_format, output_file))
