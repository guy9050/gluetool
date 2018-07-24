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
#
# I'm leaving the necessary code in the setup.py but it's not being called.
#

# try:
#    import gluetool
#
# except ImportError:
#    print >> sys.stderr, 'Cannot import gluetool, please install it to install/build this package.'
#    sys.exit(1)

INSTALL_REQUIRES = [
    "ansible==2.4.2.0",
    "composeci",  # composetest
    "cmd2==0.8.6",
    "beautifulsoup4==4.5.3",
    "python-dateutil==2.6.1",
    "docker-pycreds==0.2.1",
    "docker==2.5.1",
    "enum34==1.1.6",
    "jenkins-job-builder==1.6.2",
    "jenkinsapi==0.3.3",
    "koji",  # composetest
    "mako==1.0.6",
    # available over external URL => dependency_links => they does not seem to work when installing via pip :/
    #    "mysql-connector-python==2.0.4",
    "packaging==16.8",
    "proton==0.8.8",
    "psycopg2==2.7.3.1",
    "pyOpenSSL==17.0.0",
    "pycurl==7.43.0",
    "python-glanceclient==2.8.0",
    "python-novaclient==7.1.0",
    "python-openstackclient==3.9.0",
    "python-qpid-proton==0.18.1",
    "requests-kerberos==0.11.0",
    "requests==2.18.4",
    "requestsexceptions==1.2.0",
    "stomp.py==4.1.17",
    "urlgrabber==3.10.2",
    # cannot use the "nitrate" - pip would then fail to build pycurl with the correct SSL backend
    # "git+https://github.com/psss/python-nitrate.git@1.3-2#egg=nitrate"
    "nitrate==1.3.1"
]

DEPENDENCY_LINKS = [
    "git+https://gitlab.cee.redhat.com/bkabrda/composeci.git#egg=composeci-9876543210",  # composetest
    "https://releases.pagure.org/koji/koji-1.16.0.tar.bz2#egg=koji-9876543210",  # composetest
    # pylint: disable=line-too-long
    #    "http://cdn.mysql.com/Downloads/Connector-Python/mysql-connector-python-2.0.4.zip#md5=3df394d89300db95163f17c843ef49df&egg=mysql-connector-python-2.0.4"  # Ignore PEP8Bear
]


# Generate list of data files - modules and their moduleinfo files - for a category.
# That is basically a list of all files in a directory (which *is* the category name)
# under gluetool_modules. Ignore other extensions, __init__.py - these are not modules
# - and also skip _mysql module which has dependency issues :/ And sort those data files.
#
# Output is what setup expects to get when dealing with data files:
# [data file directory, [data file #1, data file #2, ...]]
def _data_files(category_slug):
    dirpath = os.path.join('gluetool_modules', category_slug)

    return [
        dirpath,
        sorted([
            os.path.join(dirpath, filepath) for filepath in os.listdir(dirpath)
            # pylint: disable=line-too-long
            if (filepath.endswith('.py') or filepath.endswith('.moduleinfo')) and filepath != '__init__.py' and not filepath.startswith('_mysql.')
        ])
    ]


# List of module categories, aka. subdirectories of gluetool_modules.
CATEGORIES = [
    'build_on_commit',
    'database',
    'dispatchers',
    'helpers', 'helpers/jenkins', 'helpers/notify_email', 'helpers/beaker',
    'infrastructure',
    'provision',
    'static_analysis', 'static_analysis/covscan', 'static_analysis/rpmdiff',
    'testing', 'testing/beaker', 'testing/composetest', 'testing/openstack', 'testing/restraint'
]

DATA_FILES = [
    _data_files(category) for category in CATEGORIES
]

# def get_install_requires():
#    requirements = []
#
#    for root, _, files in os.walk('gluetool_modules'):
#        for filename in files:
#            if not filename.endswith('.moduleinfo'):
#                continue
#
#            info = gluetool.utils.load_yaml(os.path.join(root, filename))
#            if 'dependencies' not in info or 'pip' not in info['dependencies']:
#                continue
#
#            requirements += info['dependencies']['pip']
#
#    requirements = sorted(requirements)
#
#    # filter out packages that are not in PYPI...
#    dependency_links = [requirement for requirement in requirements if requirement.startswith('http')]
#    # ... and remove them from requirements
#    requirements = [requirement for requirement in requirements if requirement not in dependency_links]
#
#    return (requirements, dependency_links)


# def get_data_files():
#    file_collection = collections.defaultdict(list)
#
#    for root, _, files in os.walk('gluetool_modules'):
#        for filename in files:
#            if not filename.endswith('.moduleinfo'):
#                continue
#
#            file_collection[root].append(os.path.splitext(filename)[0])
#            continue
#
#    data_files = []
#
#    for root, names in file_collection.iteritems():
#        data_files.append((
#            'gluetool_modules/{}'.format(root),
#            # pylint: disable=line-too-long
#            ['{}/{}.py'.format(root, name) for name in names] + ['{}/{}.moduleinfo'.format(root, name) for name in names]
#        ))
#
#    return data_files


if __name__ == '__main__':
    # gluetool.log.Logging.create_logger()

    # INSTALL_REQUIRES, DEPENDENCY_LINKS = get_install_requires()
    # DATA_FILES = get_data_files()

    # pylint: disable=line-too-long
    # for label, data in [('Install requires', INSTALL_REQUIRES), ('Dependency links', DEPENDENCY_LINKS), ('Data files', DATA_FILES)]:  # Ignore PEP8Bear
    #    print '{}:'.format(label)
    #    print gluetool.log.format_dict(data)
    #    print

    setup(name='gluetool_modules',
          setup_requires=['setuptools_scm'],
          use_scm_version=True,
          packages=[
              'gluetool_modules'
          ] + [
              'gluetool_modules.{}'.format(subpackage) for subpackage in [
                  'testing',
                  'testing.beaker',
                  'testing.restraint',
                  'testing.openstack',
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
                  'build_on_commit',
                  'provision'
              ]
          ],
          data_files=DATA_FILES,
          install_requires=[
              'gluetool',
              'citool',
              'sphinx-argparse==0.2.0',
              'sphinxcontrib-programoutput==0.11'
          ] + INSTALL_REQUIRES,
          dependency_links=DEPENDENCY_LINKS,
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
