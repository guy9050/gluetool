import json
import pytest

import libci
import libci.modules.testing.testing_results

from . import create_module


@pytest.fixture(name='module')
def fixture_module():
    # pylint: disable=unused-argument

    return create_module(libci.modules.testing.testing_results.TestingResults)


@pytest.fixture(name='result')
def fixture_result():
    # pylint: disable=unused-argument

    return libci.results.TestResult('dummy', 'PASS')


def assert_results(results, length=0, model=None):
    assert isinstance(results, list)
    assert len(results) == length

    if model is not None:
        assert results is model


def test_sanity(module, result):
    """
    Basic sanity test - excercise shared function and add a result.
    """

    ci, _ = module

    assert ci.has_shared('results') is True

    results1 = ci.shared('results')
    assert_results(results1)

    results1.append(result)

    results2 = ci.shared('results')
    assert_results(results2, length=1, model=results1)

    assert result in results2


def test_store_not_set(log, module, result):
    """
    Module should not save anything when --results-file is not set.
    """

    ci, mod = module

    ci.shared('results').append(result)

    # pylint: disable=protected-access
    assert mod._config['results-file'] is None

    # clear caplog because ci.shared might have produced something
    log.clear()
    mod.destroy()

    # when results are saved, module reports that to user
    assert not log.records


def test_store(log, module, result, tmpdir):
    """
    Store test result into a file.
    """

    ci, mod = module

    ci.shared('results').append(result)

    results_file = tmpdir.join('dummy-results.json')
    # pylint: disable=protected-access
    mod._config['results-file'] = str(results_file)

    # clear caplog because ci.shared might have produced something
    log.clear()
    mod.destroy()

    assert len(log.records) == 1
    assert log.records[0].message == "Results saved into '{}'".format(str(results_file))

    with open(str(results_file), 'r') as f:
        written_results = json.load(f)

    assert isinstance(written_results, list)
    assert len(written_results) == 1

    assert written_results[0] == result.serialize()


def test_init_file_not_set(module):
    """
    Module should not do anything when --init-file is not set.
    """

    ci, mod = module

    results1 = ci.shared('results')
    assert_results(results1)

    # pylint: disable=protected-access
    assert mod._config['init-file'] is None
    mod.execute()

    assert_results(ci.shared('results'), model=results1)


def test_init_file(module, result, tmpdir):
    """
    Try to load perfectly fine test result using --init-file.
    """

    ci, mod = module

    init_file = tmpdir.join('fake-results.json')
    init_file.write(json.dumps([result.serialize()]))

    # pylint: disable=protected-access
    mod._config['init-file'] = str(init_file)

    mod.execute()

    results = ci.shared('results')

    assert_results(results, length=1)
    assert results[0].serialize() == result.serialize()


def test_init_file_broken(module, result, tmpdir):
    """
    Try to load broken test result (missing a key).
    """

    ci, mod = module

    serialized_result = result.serialize()
    del serialized_result['test_type']

    init_file = tmpdir.join('bad-result.json')
    init_file.write(json.dumps([serialized_result]))

    # pylint: disable=protected-access
    mod._config['init-file'] = str(init_file)

    with pytest.raises(libci.CIError, match=r"^init file is invalid, key 'test_type' not found$"):
        mod.execute()

    assert_results(ci.shared('results'))


def test_init_file_ioerror(module, monkeypatch, result, tmpdir):
    """
    Try to deal with IOError raised during --init-file loading.
    """

    ci, mod = module

    init_file = tmpdir.join('bad-result.json')
    init_file.write(json.dumps([result.serialize()]))

    # pylint: disable=protected-access
    mod._config['init-file'] = str(init_file)

    def buggy_load(stream):
        # pylint: disable=unused-argument

        raise IOError('this is a fake IOError')

    monkeypatch.setattr(json, 'load', buggy_load)

    with pytest.raises(libci.CIError, match=r"^this is a fake IOError$"):
        mod.execute()

    monkeypatch.undo()

    assert_results(ci.shared('results'))
