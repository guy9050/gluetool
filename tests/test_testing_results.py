import json
import pytest

import libci
import libci.modules.testing.testing_results

from . import create_module


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.testing.testing_results.TestingResults)[1]


@pytest.fixture(name='result')
def fixture_result(module):
    # pylint: disable=unused-argument

    return libci.results.TestResult(module.ci, 'dummy', 'PASS')


def assert_results(results, length=0, model=None):
    assert isinstance(results, list)
    assert len(results) == length

    if model is not None:
        assert results is model


def test_shared(module):
    assert module.has_shared('results')

    # pylint: disable=protected-access
    assert module.results() is module._results


def test_updatable(module, result):
    """
    Basic sanity test - excercise shared function and add a result.
    """

    results1 = module.results()
    results1.append(result)

    results2 = module.results()

    assert_results(results2, length=1, model=results1)
    assert result in results2


@pytest.mark.parametrize('input_data, expected', [
    ('  foo :  bar', [('foo', 'bar')]),
    ('  foo :  bar  , baz  : mark  ', [('foo', 'bar'), ('baz', 'mark')]),
    (['  foo :  bar  ', '  baz : mark '], [('foo', 'bar'), ('baz', 'mark')])
])
def test_parse_formats(module, input_data, expected):
    # pylint: disable=protected-access
    module._config['foo'] = input_data

    assert module._parse_formats('foo') == expected


def test_serialize_json(tmpdir, module, result):
    output_file = tmpdir.join('out.json')

    # pylint: disable=protected-access
    module._results.append(result)

    with open(str(output_file), 'w') as f:
        module._serialize_to_json(f)
        f.flush()

    with open(str(output_file), 'r') as f:
        assert result.serialize('json') == libci.utils.load_json(f)[0]


def test_unknown_serializer(module):
    # pylint: disable=protected-access
    module._config['results-file'] = ['foo:bar']

    with pytest.raises(libci.CIError, match=r"Output format 'foo' is not supported"):
        module.destroy()


def test_store_not_set(log, module):
    """
    Module should not save anything when --results-file is not set.
    """

    # pylint: disable=protected-access
    module._config['results-file'] = []

    module.destroy()

    assert log.records[-1].message == 'No results file set.'


def test_store_dryrun(log, module):
    # pylint: disable=protected-access
    module._config['results-file'] = ['foo:bar']
    module.ci._dryrun_level = libci.ci.DryRunLevels.DRY

    module.destroy()

    assert log.records[-1].message == 'Exporting results into a file is not allowed by current dry-run level'


def test_store(log, module, result, tmpdir):
    """
    Store test result into a file.
    """

    json_file = tmpdir.join('out.json')
    xunit_file = tmpdir.join('out.xml')

    # pylint: disable=protected-access
    module._results.append(result)

    module._config['results-file'] = ['json:{}'.format(str(json_file)), 'xunit:{}'.format(str(xunit_file))]

    module.destroy()

    assert log.match(message="Results in format 'json' saved into '{}'".format(str(json_file)))
    assert log.match(message="Results in format 'xunit' saved into '{}'".format(str(xunit_file)))

    with open(str(json_file), 'r') as f:
        written_results = libci.utils.load_json(f)

    assert isinstance(written_results, list)
    assert len(written_results) == 1
    assert written_results[0] == result.serialize('json')


def test_init_file_not_set(module):
    """
    Module should not do anything when --init-file is not set.
    """

    results1 = module.results()
    assert_results(results1)

    # pylint: disable=protected-access
    assert module._config['init-file'] is None
    module.execute()

    assert_results(module.results(), model=results1)


def test_init_file(module, result, tmpdir):
    """
    Try to load perfectly fine test result using --init-file.
    """

    init_file = tmpdir.join('fake-results.json')
    init_file.write(json.dumps([result.serialize('json')]))

    # pylint: disable=protected-access
    module._config['init-file'] = 'json:{}'.format(str(init_file))

    module.execute()

    results = module.results()

    assert_results(results, length=1)
    assert results[0].serialize('json') == result.serialize('json')


def test_init_file_broken(module, result, tmpdir):
    """
    Try to load broken test result (missing a key).
    """

    serialized_result = result.serialize('json')
    del serialized_result['test_type']

    init_file = tmpdir.join('bad-result.json')
    init_file.write(json.dumps([serialized_result]))

    # pylint: disable=protected-access
    module._config['init-file'] = 'json:{}'.format(str(init_file))

    with pytest.raises(libci.CIError, match=r"^init file is invalid, key 'test_type' not found$"):
        module.execute()

    assert_results(module.results())


def test_init_file_ioerror(module, monkeypatch, result, tmpdir):
    """
    Try to deal with IOError raised during --init-file loading.
    """

    init_file = tmpdir.join('bad-result.json')
    init_file.write(json.dumps([result.serialize('json')]))

    # pylint: disable=protected-access
    module._config['init-file'] = 'json:{}'.format(str(init_file))

    def buggy_load(stream):
        # pylint: disable=unused-argument

        raise IOError('this is a fake IOError')

    monkeypatch.setattr(json, 'load', buggy_load)

    with pytest.raises(libci.CIError, match=r"^this is a fake IOError$"):
        module.execute()

    monkeypatch.undo()

    assert_results(module.results())
