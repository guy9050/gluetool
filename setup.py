# import collections
# import sys

import os
from setuptools import setup


DESCRIPTION = 'Gluetool & Citool modules'


#
# Following structures could be generated automatically, but that requires YAML parser *during
# the build time*, e.g. in the form of ``gluetool.utils.load_yaml``. I don't know any reliable
# way how to install it - there's a incoming support for pyproject.toml in pip but that's not
# widely supported yet :/ Therefore, I ask developers to update these lists until we solve
# this instance of chicken-and-egg problem.

INSTALL_REQUIRES = [
    # required by all...
    'typing==3.7.4',
    'typing-extensions==3.7.4',

    # required to enable tracing - not included in gluetool, it's optional
    "jaeger-client==4.0.0",

    # required to build documentation - workaround, this will end up in gluetool upstream
    "commonmark==0.8.0",

    "ansible==2.8.5",

    "cmd2==0.8.6",
    "beautifulsoup4==4.6.3",
    "python-dateutil==2.6.1",
    "docker==3.5.1",  # docker
    "docker-pycreds==0.3.0",  # docker
    "enum34==1.1.6",
    "fmf==0.6.1",  # test-batch-planner
    'futures==3.2.0; python_version == "2.7"',  # restraint-scheduler
    "GitPython==2.1.15",  # dist-git
    "gitdb2==2.0.6",  # dist-git
    "inotify==0.2.10",  # sti
    "jenkins-job-builder==1.6.2",
    "jenkinsapi==0.3.8",
    "jq==0.1.6",
    "koji",
    "mako==1.0.6",
    "mysql-connector-python==8.0.13",  # mysql
    "packaging==17.1",
    "proton==0.8.8",
    "psycopg2==2.8.3",
    "pyOpenSSL==17.0.0",
    "pycurl==7.43.0",
    "pymemcache==2.0.0",  # memcache
    "python-glanceclient==2.8.0",
    "python-neutronclient==7.1.0",
    "python-novaclient==7.1.0",
    "python-openstackclient==3.9.0",
    "python-qpid-proton==0.18.1",
    "requests-kerberos==0.11.0",
    "requests==2.19.1",
    "requestsexceptions==1.2.0",
    "rpm-py-installer==0.7.1",
    "six==1.12.0",
    "stomp.py==4.1.17",
    "urlgrabber==3.10.2",
    "warlock==1.2.0",
    # cannot use the "nitrate" - pip would then fail to build pycurl with the correct SSL backend
    # "git+https://github.com/psss/python-nitrate.git@1.3-2#egg=nitrate"
    "nitrate==1.3.1"
]


# Generate list of data files - modules and their moduleinfo files - for a category.
# That is basically a list of all files in a directory (which *is* the category name)
# under gluetool_modules. Ignore other extensions, __init__.py - these are not modules.
# And sort those data files.
#
# Output is what setup expects to get when dealing with data files:
# [data file directory, [data file #1, data file #2, ...]]
def _data_files(category_slug):
    dirpath = os.path.join('gluetool_modules', category_slug)

    return [
        dirpath,
        sorted([
            os.path.join(dirpath, filepath) for filepath in os.listdir(dirpath)
            if (filepath.endswith('.py') or filepath.endswith('.moduleinfo')) and filepath != '__init__.py'
        ])
    ]


# List of module categories, aka. subdirectories of gluetool_modules.
CATEGORIES = [
    'build_on_commit',
    'database',
    'dispatchers',
    'helpers', 'helpers/jenkins', 'helpers/notify_email', 'helpers/beaker',
    'infrastructure',
    'pipelines',
    'provision',
    'static_analysis', 'static_analysis/covscan', 'static_analysis/rpmdiff', 'static_analysis/rpminspect',
    'testing', 'testing/beaker', 'testing/openstack', 'testing/pull_request_builder'
]

DATA_FILES = [
    _data_files(category) for category in CATEGORIES
]


if __name__ == '__main__':
    setup(name='gluetool_modules',
          packages=[
              'gluetool_modules',
              'gluetool_modules.libs'
          ] + [
              'gluetool_modules.{}'.format(subpackage) for subpackage in [
                  'testing',
                  'testing.beaker',
                  'testing.openstack',
                  'testing.pull_request_builder',
                  'tests',
                  'dispatchers',
                  'helpers',
                  'helpers.beaker',
                  'helpers.jenkins',
                  'helpers.notify_email',
                  'infrastructure',
                  'database',
                  'static_analysis',
                  'static_analysis.covscan',
                  'static_analysis.rpmdiff',
                  'static_analysis.rpminspect',
                  'build_on_commit',
                  'pipelines',
                  'provision'
              ]
          ],
          data_files=DATA_FILES,
          install_requires=[
              'gluetool',
              'citool @ git+https://gitlab.cee.redhat.com/baseos-qe/citool.git#egg=citool',
              'sphinx-argparse==0.2.0',
              'sphinxcontrib-programoutput==0.11'
          ] + INSTALL_REQUIRES,
          description=DESCRIPTION,
          long_description=DESCRIPTION,
          author='Miroslav Vadkerti',
          author_email='mvadkert@redhat.com',
          license='ISC license',
          platforms='UNIX',
          url='TODO',
          classifiers=[
              'Development Status :: 3 - Alpha',
              'Environment :: Console',
              'Intended Audience :: Developers',
              'Intended Audience :: System Administrators',
              'License :: OSI Approved :: ISC License (ISCL)',
              'Operating System :: POSIX',
              'Programming Language :: Python',
              'Programming Language :: Python :: 2.6',
              'Programming Language :: Python :: 2.7',
              'Topic :: Software Development',
              'Topic :: Software Development :: Libraries :: Python Modules',
              'Topic :: Software Development :: Quality Assurance',
              'Topic :: Software Development :: Testing',
              'Topic :: System',
              'Topic :: System :: Archiving :: Packaging',
              'Topic :: System :: Installation/Setup',
              'Topic :: System :: Shells',
              'Topic :: System :: Software Distribution',
              'Topic :: Terminals',
          ])
