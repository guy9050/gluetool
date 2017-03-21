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

To begin digging into `citool` sources, get yourself a `virtualenv` utility, and create your development environment:

```
  mkvirtualenv -p /usr/bin/python2.7 <virtualenv-dir>
  git clone gitlab:<your username>/<your fork name>
  cd citool
  python setup.py develop
```

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
