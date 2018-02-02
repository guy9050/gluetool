citool - Continuous Integration Swiss Army Knife
------------------------------------------------

``citool`` is a command line centric CI framework used to implement (not only) BaseOS CI

Documentation
-------------

For more information see generated citool's documetation

http://liver3.lab.eng.brq.redhat.com/~citool-doc/

For more on pipelines, see libci/data/README.md.

Testing
-------

How to run one concrete test
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To run a concrete test, you can call tox this way.

    tox -e py27-unit-tests -- gluetool_modules/tests/test_wow.py::test_with_basic_params
