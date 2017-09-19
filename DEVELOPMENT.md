# Development


## Environment

Before moving on to the actual setup, there are few important notes:

* **The only supported and (sort of tested) way of instalation and using `citool` is a separate virtual environment!** It may be possible to install `citool` directly somewhere into your system but we don't recommend that, we don't use it that way, and we don't know what kind of hell you might run into. Please, stick with `virtualenv`.

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

  - `ansible-playbook`

  - system packages - it is either impossible or impractical to use their Python counterpart, or they are required to
    build a Python package required by `citool`. In some cases, on recent Fedora (26+) for example, it's been shown
    for some packages their `compat-*` variant might be needed. See the optional ``Bootstrap system environment`` step
    bellow.

  - you'll need RH CA certificates, some pieces of our infrastructure work on HTTPS. If you don't have the certs
    installed already (check your `/etc/ssl`), fetch them (`root` required):

    ```
      curl -o /etc/pki/ca-trust/source/anchors/RH-IT-Root-CA.crt https://password.corp.redhat.com/RH-IT-Root-CA.crt
      curl -o /etc/pki/ca-trust/source/anchors/Eng-CA.crt https://engineering.redhat.com/Eng-CA.crt
      update-ca-trust
    ```

0. (optional) Bootstrap system environment:

   Following steps are necessary to install requirements when installing ``citool`` on different distributions:

   **RHEL 7.4**

   ```
     yum install -y krb5-devel libcurl-devel libxml2-devel openssl-devel python-devel
     curl "https://bootstrap.pypa.io/get-pip.py" -o "get-pip.py" && python get-pip.py && rm -f get-pip.py
     pip install -U setuptools
     pip install ansible virtualenv
   ```

   **Fedora 26**

   ```
   dnf install -y ansible krb5-devel libselinux-python python2-virtualenv /usr/lib/rpm/redhat/redhat-hardened-cc1
   dnf install -y --allowerasing compat-openssl10-devel
   pip install -U setuptools
   ```

1. Create a virtual environment:
   ```
     virtualenv -p /usr/bin/python2.7 <virtualenv-dir>
     . <virtualenv-dir>/bin/activate
   ```

2. Clone `citool` repository - your working copy:
   ```
     git clone gitlab:<your username>/<your fork name>
     cd citool
   ```

3. Install `citool`:
   ```
     /usr/bin/ansible-playbook ./install.yml
   ```

   **Be warned:** read the messages reported by this step - `install.yml` playbook checks for necessary system packages, and reports any missing pieces. **It does not install them!** We don't want to mess with your system setup, as we try to stay inside our little own virtualenv, but the playbook will try to provide hints on what packages might solve the issue.

4. (optional) Activate Bash completion
   ```
     python bash_completion.py
     mv citool $VIRTUAL_ENV/bin/citool-bash-completition
     echo "source $VIRTUAL_ENV/bin/citool-bash-completition" >> $VIRTUAL_ENV/bin/activate
   ```

5. Re-activate virtualenv

   Since step #1 your `citool` virtualenv is active, but `citool`'s installation made some changes to the `activate`
   script, therefore it's necessary to re-activate the virtualenv before actually doing stuff with `citool`:

   ```
   deactivate
   . <virtualenv-dir>/bin/activate
   ```

6. Fetch configuration

   `citool` looks for its configuration in `~/.citool.d`. Easy way to start out is to simply get a clone of `development` branch of upstream configuration:

   ```
   git clone gitlab:baseos-ci/citool-config
   cd citool-config
   git checkout development
   ```

   And symlink it to `~/.citool.d`.

   You can use other branches as well, e.g. `staging`, or modify your local configuration heavily, if you know what you're doing.


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

The test suite is governed by `tox` and `py.test`. Before running the test suite, you have to install `tox`:

```
  pip install tox virtualenv
```

Tox can be easily executed by:

```
  tox
```

Tox also accepts additional options which are then passed to `py.test`:

```
  tox -- --cov=libci --cov-report=html:coverage-report
```


Tox creates (and caches) virtualenv for its test runs, and uses them for running the tests. It integrates multiple
different types of test (you can see them by running `tox -l`).


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
