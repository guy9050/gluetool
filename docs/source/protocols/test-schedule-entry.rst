Test Schedule Entry Protocol
============================

To specify what tests should be ran, in what environment and which test runner should be used, modules create and process a "test schedule" - a list of "test schedule entries".

.. note::

   This is effectively a work in progress - it is motivated by a need to separate Restraint, OpenStack, ``workflow-tomorrow`` and other tools that
   made assumptions which are no longer suitable for the modern times.


Query
-----

``create_test_schedule`` shared function is used to obtain a test schedule.


Packet
------

A test schedule is represented by a ``list`` of objects with following attributes and methods.

.. py:attribute:: id

   Identification of the entry among its siblings from the plan. Used for logging purposes.

.. py:attribute:: testing_environment

   Description of the testing environment to run tests in, in a form of `Testing Environment Protocol </protocols/testing-environment>`.

.. py:attribute:: guest

  :py:class:`libci.guest.Guest` instance which should be used to run the tests on. May be unset when there was no guest assigned yet.

.. py:attribute:: package

   The description of the tests, in a form the appointed test runner understands. When given this value, the test runner would run the required tests.

.. py:method:: debug(s), info(s), warn(s), error(s), exception(s)

   Logging methods. Add special context to log records they produce, describing the schedule entry.

.. py:method:: log(log_fn: callable=None)

   Called to log the configuration of the schedule entry. When ``log_fn`` is not set, entry's ``debug`` method is used.
