Welcome to citool's documentation!
==================================

The ``citool`` command line tool is an automation tool constructing a sequential pipeline on command line. It is able to implement any sequential testing process divided into :doc:`modules <howto-modules>` with minimal interaction, glued together on the command line. The ``citool`` uses the :doc:`libci command-line centric modular framework <framework>` for implementation. The framework does not directly implement any testing specific functionality and is generic. The tool optionally integrates with `Sentry.io <https://sentry.io>`_ error logging platform for reporting issues, very useful when running ``citool`` in big.

The cool thing about having the pipeline on command line is that it can be easily copy-pasted to a localhost shell for debugging/development or the pipeline can be easily customized if needed.

Installation
------------

If you want to install citool on your machine, please follow our :doc:`DEVELOPMENT` readme in the project root folder.


Table of contents
-----------------

.. toctree::
   :maxdepth: 1

   framework 
   modules
   howto-modules
   howto-tests
   howto-docs
   protocols/protocols
   DEVELOPMENT


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
