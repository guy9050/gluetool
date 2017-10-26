import re

from setuptools import setup

DESCRIPTION = 'CI Tool - Continuous Integration Swiss Army Knife'


def get_version():
    """
    Fetch version from ``libci/version.py``.
    """

    with open('libci/version.py', 'r') as f:
        data = f.read()

    match = re.search(r"__version__\s*=\s*'(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\'", data)
    if match is None:
        raise Exception('Cannot parse gluetool version!')

    matches = match.groupdict()
    return (int(matches['major']), int(matches['minor']))


if __name__ == '__main__':
    MAJOR, MINOR = get_version()

    setup(name='citool',
          version='{0}.{1}'.format(MAJOR, MINOR),
          packages=['libci'],
          entry_points={
              'console_scripts': {
                  'citool = libci.tool:main'
              }
          },
          install_requires=[
              # http://liver3.brq.redhat.com/gluetool/packages/testing/gluetool-1.0-py2-none-any.whl
              # /home/mprchlik/virtualenvs/citool-gluetoolize-gluetool/gluetool/dist/gluetool-1.0-py2-none-any.whl
              'gluetool'
          ],
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
