Development
===========

Environment
-----------

Before moving on to the actual setup, there are few important notes:

-  **The only supported and (sort of tested) way of installation and
   using ``citool`` is a separate virtual environment!** It is possible
   to install ``citool`` & modules somewhere into your system but we don't
   recommend that, we don't use it that way when developing things, and we
   don't know what kind of hell you might run into. Please, stick with
   ``virtualenv``.

-  The tested distributions (as in "we're using these") are either
   recent Fedora, RHEL or CentOS. You could try to install ``citool``
   in a different environment - or even development trees of Fedora, for
   example - please, make notes about differences, and it'd be awesome
   if your first merge request could update this file :)

Requirements
------------

To begin digging into sources, there are few requirements:

-  ``virtualenv`` utility

-  ``ansible-playbook``

-  system packages - it is either impossible or impractical to use their
   Python counterpart, or they are required to build a Python package. In
   some cases, on recent Fedora (26+) for example, it's been shown for some
   packages their ``compat-*`` variant might be needed. See the optional
   ``Bootstrap system environment`` step bellow.

Installation
------------

0. (optional) Bootstrap system environment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Following steps are necessary to install requirements when installing
``citool`` on different distributions:

**RHEL 7.4**

.. code-block:: bash

    yum install -y krb5-devel libcurl-devel libxml2-devel openssl-devel python-devel
    curl "https://bootstrap.pypa.io/get-pip.py" -o "get-pip.py" && python get-pip.py && rm -f get-pip.py
    pip install -U setuptools
    pip install ansible virtualenv

**Fedora 26**

.. code-block:: bash

    dnf install -y ansible krb5-devel libselinux-python python2-virtualenv /usr/lib/rpm/redhat/redhat-hardened-cc1
    dnf install -y --allowerasing compat-openssl10-devel
    pip install -U setuptools

1. Create a virtual environment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    virtualenv -p /usr/bin/python2.7 <virtualenv-dir>
    . <virtualenv-dir>/bin/activate


2. Install ``gluetool``
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   git clone https://github.com/gluetool/gluetool.git gluetool
   pushd gluetool && python setup.py develop && popd


3. Install ``citool``
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    git clone git@gitlab.cee.redhat.com:baseos-qe/citool.git citool
    pushd citool && python setup.py develop && popd

4. Clone ``gluetool-modules``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   git clone git@gitlab.cee.redhat.com:baseos-qe/gluetool-modules.git gluetool-modules

5. Install requirements
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   pushd gluetool-modules && /usr/bin/ansible-playbook ./inject-extra-requirements.yml && popd

**Be warned:** read the messages reported by this step - ``inject-extra-requirements.yml``
playbook checks for necessary system packages, and reports any missing
pieces. **It does not install them!** - we don't want to mess up your
system setup, as we try to stay inside our little own virtualenv, but
the playbook will try to provide hints on what packages might solve the
issue.

6. Install ``gluetool-modules``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   pushd gluetool-modules && python setup.py develop && popd


7. Re-activate virtualenv
~~~~~~~~~~~~~~~~~~~~~~~~~

Since step #1 your virtualenv is active, but installation made some changes to the ``activate`` script, therefore
it's necessary to re-activate the virtualenv before actually doing stuff:

.. code-block:: bash

    deactivate
    . <virtualenv-dir>/bin/activate

8. Add configuration
~~~~~~~~~~~~~~~~~~~~~~

``citool`` looks for its configuration in ``~/.citool.d``. Add configuration
for the modules according to your preference:

.. code-block:: bash

   git clone -b production https://gitlab.cee.redhat.com/baseos-qe/citool-config ~/.citool.d

9. Add local configuration (optional)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A configuration you cloned from remote repository in step #8 is often tailored for other task (e.g. CI) while providing reasonable functionality when used locally. To tweak things for you, you can create a local configuration ``citool`` looks for configuration files in its working directory as well, i.e. when running from your ``gluetool-modules`` clone, it looks for ``.gluetool.d`` (or ``.citool.d`` directory).

.. code-block:: bash

   pushd gluetool-modules
   mkdir .gluetool.d
   cat << EOF > .gluetool.d/gluetool
   [default]
   output = citool-debug.txt
   colors = yes
   module-path = <location of your gluetool clone>/gluetool_modules, ./gluetool_modules
   EOF
   popd


9. Test ``citool``
~~~~~~~~~~~~~~~~~~

Now every time you activate your new virtualenv, you should be able to
run ``citool``:

.. code-block:: bash

    citool -h
    usage: citool [opts] module1 [opts] [args] module2 ...

    optional arguments:
    ...


.. code-block:: bash

   citool -l
   ... pile of modules ...



Test suites
-----------

The test suite is governed by ``tox`` and ``py.test``. Before running
the test suite, you have to install ``tox``:

.. code-block:: bash

    pip install tox

Tox can be easily executed by:

.. code-block:: bash

    tox

Tox also accepts additional options which are then passed to
``py.test``:

.. code-block:: bash

    tox -- --cov=libci --cov-report=html:coverage-report

Tox creates (and caches) virtualenv for its test runs, and uses them for
running the tests. It integrates multiple different types of test (you
can see them by running ``tox -l``).


Documentation
-------------

Auto-generated documentation is located in ``docs/`` directory. To
update your local copy, run these commands:

.. code-block:: bash

    ansible-playbook ./generate-docs.yaml

Then you can read generated docs by opening ``docs/build/html/index.html``.


Troubleshooting
---------------

Issues with pycurl
~~~~~~~~~~~~~~~~~~

In case you encounter tracebacks when importing pycurl, similar to this one:

.. note::

    ImportError: pycurl: libcurl link-time ssl backend (openssl) is different from compile-time ssl backend (nss)

This is caused by mismatch of the SSL library which libcurl package is compiled against and pycurl module's compile time library. To resolve, make sure that your PYCURL_SSL_LIBRARY environment variable is correctly set. In case if your libcurl package requires "libnss*.so" library, the value should be "nss". In case it requires "libssl*.so" library, the value should be "openssl":

.. code-block:: bash

    rpm -qR libcurl
    env | grep PYCURL_SSL_LIBRARY

Note that this environment variable is added to the virtualenv activate script in step 5. of this guide. To reinstall pycurl use these commands:

.. code-block:: bash

    pip uninstall pycurl; pip install --no-cache-dir pycurl

To verify that your pycurl works, use this command:

.. code-block:: bash

    python -c "import pycurl"
