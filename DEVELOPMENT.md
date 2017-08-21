# Development


## Environment

Before moving on to the actual setup, there are few important notes:

* **The ony supported and (sort of tested) way of instalation and using `citool` is a separate virtual environment!** It may be possible to install `citool` directly somewhere into your system but we don't recommend that, we don't use it that way, and we don't know what kind of hell you might run into. Please, stick with `virtualenv`.

* The tested distributions (as in "we're using these") are either recent Fedora, RHEL or CentOS. Should you try to install `citool` in a different environment - or even development trees of Fedora, for example - please, make notes about differencies, and it'd be awesome if your first merge request could update this file :)


Ok, let's say you have already your own `citool` fork, and you've set up gitlab in your `.ssh/config`:

### Optional - `.ssh/config`

You can add `gitlab.cee.redhat.com` into your `.ssh/config`, to make repository URLs a bit cleaner. This is optional,
if you don't have this set up, simply use `gitlab`'s full address when cloning a repository.

```
Host gitlab
  HostName gitlab.cee.redhat.com
  User git
  IdentityFile ~/.ssh/<your key>
  IdentitiesOnly yes
```

### Requirements

To begin digging into `citool` sources, there are few requirements:

  - `virtualenv` utility

  - system packages - it is either impossible or impractical to use their Python counterpart, or they are required to build a Python package required by `citool`:
    - `krb5-devel`
    - `libcurl-devel`
    - `libxml2-devel`
    - `openssl-devel`
    - `rpm-python`
    - `yum`

  - in some cases, on recent Fedora (26+), it's been shown for some packages their `compat-*` variant might be needed. Please, install these as well:
    - `compat-openssl10-devel`

  - you'll need RH CA certificates, some pieces of our infrastructure work on HTTPS. If you don't have the certs
    installed already (check your `/etc/ssl`), fetch them (`root` required):

    ```
      curl -o /etc/pki/ca-trust/source/anchors/RH-IT-Root-CA.crt https://password.corp.redhat.com/RH-IT-Root-CA.crt
      curl -o /etc/pki/ca-trust/source/anchors/Eng-CA.crt https://engineering.redhat.com/Eng-CA.crt
      update-ca-trust
    ```


```
  # create virtualenv
  virtualenv -p /usr/bin/python2.7 <virtualenv-dir>
  . <virtualenv-dir>/bin/activate

  # update pip
  pip install --upgrade pip

  # checkout citool's repo
  git clone gitlab:<your username>/<your fork name>
  cd citool

  # pycurl @ RH (or Fedora) is using nss backend, and that is controlled in its installation time
  # by environment variable PYCURL_SSL_LIBRARY. You should set it right now:
  export PYCURL_SSL_LIBRARY=nss

  # ... but adding it into your bin/activate script is a good idea as well - tox (we use it to run
  # citool's test seuite) installs its own virtualenv for the tests, and by having the pycurl variable
  # exported all the time you work in citool's virtualenv context would make sure the pycurl installed
  # in tox' own virtualenv will would use the correct backend.
  echo 'export PYCURL_SSL_LIBRARY=nss' >> $VIRTUAL_ENV/bin/activate

  # There's also a cache used by pip. In case you run into issues while running 'pip install' command bellow,
  # you might try to add '--no-cache-dir' toption, and try again.

  # install citool's requirements
  pip install -r requirements.txt

  # install koji - it needs to be downloaded and built
  ./install-koji.sh

  # rpm package is required by koji, and it seems reasonable to us the one provided by
  # system rpm. virtualenv is isolated from system libraries, therefore this symlink
  ln -s /usr/lib64/python2.7/site-packages/rpm $VIRTUAL_ENV/lib64/python2.7/site-packages

  # the same applies to yum (rpmUtils) as well
  ln -s /usr/lib/python2.7/site-packages/rpmUtils $VIRTUAL_ENV/lib64/python2.7/site-packages

  # we need qe.py as well, for tcms & wow to work correctly
  ln -s /usr/share/qa-tools/python-modules/qe.py $VIRTUAL_ENV/lib64/python2.7/site-packages

  # tell virtualenv's requests package about RH CA cert
  echo "export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-bundle.crt" >> $VIRTUAL_ENV/bin/activate

  # and install citool in development mode
  python setup.py develop
  
  # optional: activate bash completion in virtualenv
  python bash_completion.py
  mv citool $VIRTUAL_ENV/bin/citool-bash-completition
  echo "source $VIRTUAL_ENV/bin/citool-bash-completition" >> $VIRTUAL_ENV/bin/activate
```

`citool`'s modules may require additional commands as well, e.g. tools like `tcms-results` or `restraint`. You'd have
to install these tools as well to be able to use the corresponding modules.

Now every time you activate your new virtualenv, you should be able to run `citool`:

```
  $ citool -h
  usage: citool [opts] module1 [opts] [args] module2 ...

  optional arguments:
  ...

```


## Test suites

To run the tests:

```
  python setup.py test
```

Testsuite is governed by `py.test`, you can override its default arguments using `-a` option:

```
  python setup.py test -a "--cov=libci --cov-report=html:coverage-report"
```

Or, you can use the Tox. To use Tox, you have to firstly install these packages:

```
  pip install tox virtualenv
```

Tox can be easily executed by:

```
  tox -e py27
```

Tox also accepts additional options which are then passed to `py.test`:

```
  tox -e py27 -- --cov=libci --cov-report=html:coverage-report
```


While `setup.py` uses the current Python interpreter it founds in your `$PATH`, Tox creates (and caches) virtualenv
for the test run, and uses that for running the tests. It also adds few other tests that were simpler to integrate
here, e.g. YAML linter.


## Documentation

Auto-generated documentation is located in `docs/` directory. To update your local copy, run these commands:

```
  # install requirements
  pip install -r requirements.txt

  # regenerate RST sources from Python files
  sphinx-apidoc -T -e -o docs/source/ libci/

  # regenerate RST sources for citool modules
  python docs/generate-module-page.py

  # generate HTML
  make -C docs/ html
```

Then you can read generated docs by opening `docs/build/html/index.html`.
