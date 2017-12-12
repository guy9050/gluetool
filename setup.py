# import collections
# import os
# import sys

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
    "beautifulsoup4==4.5.3",
    "python-dateutil==2.6.1",
    "docker-pycreds==0.2.1",
    "docker==2.5.1",
    "enum34==1.1.6",
    "jenkins-job-builder==1.6.2",
    "jenkinsapi==0.3.3",
    "mako==1.0.6",
    # available over external URL => dependency_links => they does not seem to work when installing via pip :/
    #    "mysql-connector-python==2.0.4",
    "packaging==16.8",
    "proton==0.8.8",
    "psycopg2==2.7.3.1",
    "pyOpenSSL==17.0.0",
    "pycurl==7.43.0",
    "python-novaclient==7.1.0",
    "python-openstackclient==3.9.0",
    "python-qpid-proton==0.18.1",
    "requests-kerberos==0.11.0",
    "requests==2.18.4",
    "requestsexceptions==1.2.0",
    "retrying==1.3.3",
    "stomp.py==4.1.17",
    "urlgrabber==3.10.2",
    # cannot use the "nitrate" - pip would then fail to build pycurl with the correct SSL backend
    # "git+https://github.com/psss/python-nitrate.git@1.3-2#egg=nitrate"
    "nitrate==1.3.1"
]

DEPENDENCY_LINKS = [
    # pylint: disable=line-too-long
    #    "http://cdn.mysql.com/Downloads/Connector-Python/mysql-connector-python-2.0.4.zip#md5=3df394d89300db95163f17c843ef49df&egg=mysql-connector-python-2.0.4"  # Ignore PEP8Bear
]

DATA_FILES = [
    [
        "gluetool_modules/static_analysis/rpmdiff",
        [
            "gluetool_modules/static_analysis/rpmdiff/rpmdiff_job.py",
            "gluetool_modules/static_analysis/rpmdiff/rpmdiff.py",
            "gluetool_modules/static_analysis/rpmdiff/rpmdiff_job.moduleinfo",
            "gluetool_modules/static_analysis/rpmdiff/rpmdiff.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/static_analysis/covscan",
        [
            "gluetool_modules/static_analysis/covscan/covscan_job.py",
            "gluetool_modules/static_analysis/covscan/covscan.py",
            "gluetool_modules/static_analysis/covscan/covscan_job.moduleinfo",
            "gluetool_modules/static_analysis/covscan/covscan.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/database",
        [
            "gluetool_modules/database/postgresql.py",
            #           "gluetool_modules/database/_mysql.py",
            "gluetool_modules/database/postgresql.moduleinfo",
            #            "gluetool_modules/database/_mysql.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/helpers",
        [
            "gluetool_modules/helpers/rpmdiff_waiver.py",
            "gluetool_modules/helpers/publisher_umb_bus.py",
            "gluetool_modules/helpers/pipeline_state_reporter.py",
            "gluetool_modules/helpers/exporter_resultsdb.py",
            "gluetool_modules/helpers/envinject.py",
            "gluetool_modules/helpers/notify_recipients.py",
            "gluetool_modules/helpers/guess_openstack_image.py",
            "gluetool_modules/helpers/guest_setup.py",
            "gluetool_modules/helpers/publisher_ci_bus.py",
            "gluetool_modules/helpers/brew_tag_build.py",
            "gluetool_modules/helpers/guess_beaker_distro.py",
            "gluetool_modules/helpers/testing_thread.py",
            "gluetool_modules/helpers/simple_wow_export.py",
            "gluetool_modules/helpers/build_dependencies.py",
            "gluetool_modules/helpers/ansible.py",
            "gluetool_modules/helpers/guess_product.py",
            "gluetool_modules/helpers/execute_command.py",
            "gluetool_modules/helpers/trigger_message.py",
            "gluetool_modules/helpers/rpmdiff_waiver.moduleinfo",
            "gluetool_modules/helpers/publisher_umb_bus.moduleinfo",
            "gluetool_modules/helpers/pipeline_state_reporter.moduleinfo",
            "gluetool_modules/helpers/exporter_resultsdb.moduleinfo",
            "gluetool_modules/helpers/envinject.moduleinfo",
            "gluetool_modules/helpers/notify_recipients.moduleinfo",
            "gluetool_modules/helpers/guess_openstack_image.moduleinfo",
            "gluetool_modules/helpers/guest_setup.moduleinfo",
            "gluetool_modules/helpers/publisher_ci_bus.moduleinfo",
            "gluetool_modules/helpers/brew_tag_build.moduleinfo",
            "gluetool_modules/helpers/guess_beaker_distro.moduleinfo",
            "gluetool_modules/helpers/testing_thread.moduleinfo",
            "gluetool_modules/helpers/simple_wow_export.moduleinfo",
            "gluetool_modules/helpers/build_dependencies.moduleinfo",
            "gluetool_modules/helpers/ansible.moduleinfo",
            "gluetool_modules/helpers/guess_product.moduleinfo",
            "gluetool_modules/helpers/execute_command.moduleinfo",
            "gluetool_modules/helpers/trigger_message.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/helpers/notify_email",
        [
            "gluetool_modules/helpers/notify_email/notify_email.py",
            "gluetool_modules/helpers/notify_email/notify_email.moduleinfo",
            "gluetool_modules/helpers/notify_email/notify_email_beah_formatter.py",
            "gluetool_modules/helpers/notify_email/notify_email_beah_formatter.moduleinfo",
            "gluetool_modules/helpers/notify_email/notify_email_covscan_formatter.py",
            "gluetool_modules/helpers/notify_email/notify_email_covscan_formatter.moduleinfo",
            "gluetool_modules/helpers/notify_email/notify_email_rpmdiff_formatter.py",
            "gluetool_modules/helpers/notify_email/notify_email_rpmdiff_formatter.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/infrastructure",
        [
            "gluetool_modules/infrastructure/bus_listener.py",
            "gluetool_modules/infrastructure/openstack.py",
            "gluetool_modules/infrastructure/koji_fedora.py",
            "gluetool_modules/infrastructure/jenkins.py",
            "gluetool_modules/infrastructure/_docker.py",
            "gluetool_modules/infrastructure/bus_listener.moduleinfo",
            "gluetool_modules/infrastructure/openstack.moduleinfo",
            "gluetool_modules/infrastructure/koji_fedora.moduleinfo",
            "gluetool_modules/infrastructure/jenkins.moduleinfo",
            "gluetool_modules/infrastructure/_docker.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/dispatchers",
        [
            "gluetool_modules/dispatchers/koji_dispatcher.py",
            "gluetool_modules/dispatchers/test_batch_planner.py",
            "gluetool_modules/dispatchers/koji_dispatcher.moduleinfo",
            "gluetool_modules/dispatchers/test_batch_planner.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/build_on_commit",
        [
            "gluetool_modules/build_on_commit/build_on_commit.py",
            "gluetool_modules/build_on_commit/build_on_commit.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/testing/openstack",
        [
            "gluetool_modules/testing/openstack/openstack_job.py",
            "gluetool_modules/testing/openstack/openstack_job.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/helpers/jenkins",
        [
            "gluetool_modules/helpers/jenkins/brew_build_name.py",
            "gluetool_modules/helpers/jenkins/brew_build_name.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/testing",
        [
            "gluetool_modules/testing/testing_results.py",
            "gluetool_modules/testing/mtf.py",
            "gluetool_modules/testing/beah_result_parser.py",
            "gluetool_modules/testing/sclrun.py",
            "gluetool_modules/testing/wow.py",
            "gluetool_modules/testing/beah_xunit.py",
            "gluetool_modules/testing/testing_results.moduleinfo",
            "gluetool_modules/testing/mtf.moduleinfo",
            "gluetool_modules/testing/beah_result_parser.moduleinfo",
            "gluetool_modules/testing/sclrun.moduleinfo",
            "gluetool_modules/testing/wow.moduleinfo",
            "gluetool_modules/testing/beah_xunit.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/testing/beaker",
        [
            "gluetool_modules/testing/beaker/beaker_job.py",
            "gluetool_modules/testing/beaker/beaker.py",
            "gluetool_modules/testing/beaker/beaker_job.moduleinfo",
            "gluetool_modules/testing/beaker/beaker.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/testing/restraint",
        [
            "gluetool_modules/testing/restraint/restraint.py",
            "gluetool_modules/testing/restraint/scheduler.py",
            "gluetool_modules/testing/restraint/runner.py",
            "gluetool_modules/testing/restraint/restraint.moduleinfo",
            "gluetool_modules/testing/restraint/scheduler.moduleinfo",
            "gluetool_modules/testing/restraint/runner.moduleinfo"
        ]
    ],
    [
        "gluetool_modules/provision",
        [
            "gluetool_modules/provision/docker.py",
            "gluetool_modules/provision/docker.moduleinfo"
        ]
    ]
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
