"""
CI Tool - Continuous Integration Swiss Army Knife

Run `setup.py` to perfor various actions:
    - `python setup.py test` to run the testsuite
"""

import os
import re
import subprocess

from setuptools import setup

DESCRIPTION = 'CI Tool - Continuous Integration Swiss Army Knife'

SETUP_REQUIRES = [
    'pytest-runner'
]

INSTALL_REQUIRES = [
    'jenkinsapi',
    'PyYAML',
    'python-krbV'
]

TESTS_REQUIRE = [
    'pytest-pylint',
    'pytest-flake8',
    'pytest'
]

VERSION_FILE = os.path.join(os.path.dirname(__file__), 'libci/version.py')


def get_version():
    """
    Fetch version from git tags, or use libci/version.py when tags are not
    available (PyPi package).

    :rtype: (str, str)
    :returns: version and release, if available, or `('0.1', None)`
    """

    try:
        version_git = subprocess.check_output(['git', 'describe', '--tags']).strip()

        match = re.match(r'([0-9\.]*)-?([0-9]+)?.*', version_git)
        if match is not None:
            return (match.group(1), match.group(2))

    # pylint: disable-msg=broad-except
    except Exception:
        try:
            with open(VERSION_FILE, 'r') as f:
                version_git = f.read().strip().split('=')[-1]

            version_git = version_git.replace('\'', '')
            match = re.match(r'([0-9\.]*)-?([0-9]+)?.*', version_git)

            if match is not None:
                return (match.group(1), match.group(2))

        except IOError:
            return ('0.1', None)


def update_version(version, release):
    """
    Store version and release into `libci/version.py`.

    :param str version: Version.
    :param str release: Release, or `None` when not available.
    """

    version_msg = '# Do not edit this file, versioning is governed by git tags'

    with open(VERSION_FILE, 'w') as f:
        if release is not None:
            f.write('{0}\n__version__ = \'{1}-{2}\'\n'.format(version_msg, version, release))

        else:
            f.write('{0}\n__version__ = \'{1}\'\n'.format(version_msg, version))


VERSION, RELEASE = get_version()

if __name__ == '__main__':
    update_version(VERSION, RELEASE)

    setup(name='citool',
          # we write only the version here, release should be specified only for rpm
          version='{0}'.format(VERSION),
          setup_requires=SETUP_REQUIRES,
          install_requires=INSTALL_REQUIRES,
          tests_require=TESTS_REQUIRE,
          packages=['libci'],
          include_package_data=True,
          scripts=['bin/citool'],
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
