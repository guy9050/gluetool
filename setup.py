import os
import re
import subprocess

from setuptools import setup

description = 'CI Tool - Continuous Integration Swiss Army Knife'

setup_requires = [
    'pytest',
    'pytest-runner'
]

install_requires = [
    'jenkinsapi'
]

tests_require = [
    'pytest-pylint',
    'pytest-flake8'
]

# Fetch version from git tags, and write to version.py.
# Also, when git is not available (PyPi package), use stored version.py.
version_py = os.path.join(os.path.dirname(__file__), 'libci/version.py')

try:
    version_git = subprocess.check_output(['git', 'describe', '--tags'])
    version_git = version_git.strip()
    match = re.match('([0-9\.]*)-?([0-9]+)?.*', version_git)
    version = match.group(1)
    release = match.group(2)
except:
    try:
        with open(version_py, 'r') as fh:
            version_git = open(version_py).read().strip().split('=')[-1]
        version_git = version_git.replace('\'', '')
        match = re.match('([0-9\.]*)-?([0-9]+)?.*', version_git)
        version = match.group(1)
        release = match.group(2)
    except IOError:
        version = '0.1'
        release = None

version_msg = '# Do not edit this file, versioning is governed by git tags'
with open(version_py, 'w') as fh:
    if release:
        fh.write('{0}\n__version__ = \'{1}-{2}\''.format(version_msg, version, release))
    else:
        fh.write('{0}\n__version__ = \'{1}\''.format(version_msg, version))

if __name__ == '__main__':
    description = 'Audit test tool - a tool to make your life'
    description += ' with audit-test suite easier.'
    setup(name='citool',
          # we write only the version here, release should be specified only for rpm
          version='{0}'.format(version),
          setup_requires = setup_requires,
          install_requires=install_requires,
          tests_require = tests_require,
          packages=['libci'],
          include_package_data=True,
          scripts=['bin/citool'],
          description=description,
          long_description=description,
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
