How to: ``libci`` tests
=======================

This text is a (hopefully complete) list of best practices, dos and don'ts and tips when it comes to writing
tests for ``citool`` APIs, modules and other code. When writing - or reviewing - ``citool`` tests, please
adhere to these rules whenever possible.

.. note::

   These rules are not cast in stone - when we find out some are standing in our way to the most readable
   and usable documentation, let's just discuss the change and change what must be changed.


py.test
-------

``citool`` uses ``py.test`` framework for its test and tox to automate the running of the tests. If you're not
familiar with these tools, please see following links to get some idea:

* `py.test <https://docs.pytest.org/en/latest/>`_
* `tox <https://tox.readthedocs.io/en/latest/>`_

Also inspecting existing tests and ``tox.ini`` is a good way to find out how to do something, e.g. add new coverage
for your module.


Howto run tests?
----------------

You can either use ``setup.py``:

.. code-block:: bash

   python setup.py test


Or ``tox``:

.. code-block:: bash

   tox -e py27


Module tests should be in the same file
---------------------------------------

Tests dealing with a single module should be packed in the same file.


Test function tests one thing/code path
---------------------------------------

Avoid the temptation to put more different tests into a single test function. Test function should test a single
feature or a code path. If you're concerned about repeating setup/teardown code a lot, learn about fixtures bellow.


Use ``assert``
--------------

``py.test`` prefers to use ``assert`` keyword to actually test values, and it promotes its use by providing really
nice and helpful formatting of failures, with pointers to places where the actual values differ from expected ones.

Sometimes it's very useful to create a helper function that checks complex response, data or object state, using
multiple lower-level ``assert`` instances.


Use fixtures
------------

.. epigraph::

   The purpose of test fixtures is to provide a fixed baseline upon which tests can reliably and repeatedly execute.
   pytest fixtures offer dramatic improvements over the classic xUnit style of setup/teardown functions.

   -- py.test `documentation <https://docs.pytest.org/en/latest/fixture.html>`_

They don't lie, it's definitely worth the effort. Pretty much every test of a module's code begins with "get a fresh
instance of a module-under-test". You can call some function to create this instance, or you can use a fixture and
simply accept this instance as a argument of your test function. And so on.


.. code-block:: python

   # every test function gets its own instance of libci.CI and the module it's testing
   from . import create_module

   @pytest.fixture(name='module')
   def fixture_module():
       return create_module(libci.modules.helpers.ansible.Ansible)

   def test_sanity(module, tmpdir):
       ci, _ = module

       assert ci.has_shared('run_playbook') is True


Session fixtures belong to ``tests/conftest.py``.


Check exception messages with ``match``
---------------------------------------

Use :py:func:`pytest.raises` parameter ``match`` to assert exception messages whenever possible:

.. code-block:: python

   with pytest.raises(Exception, match=r'dummy exception'):
       foo()

Be aware that ``match`` value is actually a regular expression used to match exception's message, therefore
use Python's `raw strings <https://docs.python.org/2/reference/lexical_analysis.html#string-literals>`_, prefixed
with ``r``.


Don't be afraid of monkeypatching
---------------------------------

It helps a lot with failure injection, with observing whether your code calls other functions it's expected to call,
and other useful tricks. And all patches are undone when your test function returns.

.. code-block:: python

   # If OSEror pops up, run_command should raise CIError and re-use message from the original exception
   def faulty_popen_enoent(*args, **kwargs):
       raise OSError(errno.ENOENT, '')

   monkeypatch.setattr(subprocess, 'Popen', faulty_popen_enoent)

   with pytest.raises(libci.CIError, match=r"^Command '/bin/ls' not found$"):
       run_command(['/bin/ls'])
