# Development


## Environment

Let's say you have already your own `citool` fork, and you've set up gitlab in your `.ssh/config`:

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

To begin digging into `citool` sources, there are few requirements:

  - `virtualenv` utility
  - few packages: `libcurl-devel`, `rpm-python`, <libxml devel dependency for lxml>


```
  # create virtualenv
  virtualenv -p /usr/bin/python2.7 <virtualenv-dir>
  . <virtualenv-dir>/bin/activate

  # update pip
  pip install --upgrade pip

  # checkout citool's repo
  git clone gitlab:<your username>/<your fork name>
  cd citool

  # install citool's requirements
  pip install -r requirements.txt
  ./install-koji.sh
  pip install --global-option="--with-nss" pycurl==7.43.0  # NEEDS curl devel packages
  ln -s /usr/lib64/python2.7/site-packages/rpm $VE/lib64/python2.7/site-packages

  # tell virtualenv's requests package about RH CA cert
  echo "export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-bundle.crt" >> $VE/bin/activate

  # and install citool in development mode
  python setup.py develop
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

Or, you can use the Tox - which is in fact used when running tests by the CI:

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
