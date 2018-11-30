Changelog
=========

All notable changes to this project will be documented in this file.

The format is based on `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_.

2018-11-30
----------

Added
~~~~~

- [brew] display link to Brew website, showing details of the artifact
- [copr] display link to Copr website, showing details of the artifact
- [koji] display link to Koji website, showing details of the artifact
- [test-batch-planner] supports STI

Changed
~~~~~~~

- [mbs] update the displayed link to MBS website to match other artifact modules


2018-11-27
----------

Added
~~~~~

- [notes] new module - add various notes and warning to inform users about unexpected issues
- [notify-email] support for adding custom X-* headers
- [smtp] new module - SMTP support (sending e-mails) moved to a separate module


Fixed
~~~~~

- [beah-xunit] status and result checks must be case-insensitive
- [install-mbs-build] request repository with architectures matching given set of guests
- [mysql] fix source of connector, now using one from PyPI
- [sti] fix packaging issue


2018-11-20
----------

Changed
~~~~~~~

- ``distro`` property of testing environment renamed to ``compose`` to better reflect its content

Added
~~~~~

- [dist-git] new module - provides access to a dist-git repository of a component
- [notify-email] support ``do`` keyword in templates ("expression statement" extension)
- [static-guest] new module - wrap static guests, without any provisioning
- [sti] new module - run tests as specified by STI
- [test-scheduler] tweaked logging when provisioning and setting up guests

Fixed
~~~~~

- [build-dependencies] when primary component is listed among companions, remove it to avoid build collisions
- when running tests, ``test_`` pattern was skipped, which ignored multiple genuine modules


2018-11-13
----------

Changed
~~~~~~~

- [ansible] JSON output is the default now
- [ansible] ``run_playbook`` accepts newly also a list of playbooks
- [test-scheduler] renamed from ``restraint-scheduler``, not tied to ``restraint`` anymore
- [test-scheduler-beaker-xml] test scheduler plugin producing Restraint/Beaker XML
- [test-scheduler-runner-restraint] renamed from ``restraint-runner``
- [test-schedule-runner-restraint] report watchdog triggerings to use as a failed testing, not a crash


Added
~~~~~

- [ansible] new shared function ``detect_ansible_interpreter`` to auto-detect suitable interpreters for Ansible
- [beaker-provisioner] support direct provisioning via ``--provision``
- [beaker-provisioner] start another ``restraintd`` instance on specified port (``--restraintd-port`` option)
- [copr] handle and report failures in artifact installation as a specific exception
- [mbs] handle and report failures in artifact installation as a specific exception
- [restraint] allow change of default port on which the module expects running ``restraintd`` (``--restraintd-port`` option)
- [rules-engine] new ``filter``-like shared function, ``evaluate_filter``


Fixed
~~~~~

- [beaker] require ``evaluate_instructions`` shared function before checking degraded services
- [beaker-provisioner] check for ``extendtesttime.sh`` script before starting extend refresh loop to avoid race condition
- [docker-provisioner] updated to the latest "standards" of usage and testing environment handling
- [openstack] require ``evaluate_instructions`` shared function before checking degraded services

Removed
~~~~~~~

- [ansible] "smart" picking of failed tasks from the log was removed, detailed exception messages are no longer provided


2018-10-30
----------

Changed
~~~~~~~

- [beaker-provisioner] use PHASE to inform wow that we're provisioning guests

Added
~~~~~

- [ansible] parse failues from YAML Ansible output
- [ansible] ``cwd`` parameter to control Ansible's working directory
- [events] new module - let modules trigger and subscribe to events
- [execute-command] export functionality as a shared function
- [openstack] extract metadata and compose name from image
- [openstack] export list of guests via eval context
- [publisher-umb-bus] on error, without a link, create dummy error description

Fixed
~~~~~

- [composetest] fix handling default configuration


2018-10-23
----------

Changed
~~~~~~~

- [mbs] use full module NSVC to install it, instead of NSV
- [publisher-umb-bus] retry on *all* errors, not just on auth* related ones

Added
~~~~~

- [ansible] let user specify the inventory file instead of generating the default inventory based on given guests
- [beaker-provisioner] keep track of the age of guests in the cache
- [mbs] provide common artifact properties like ``nvr``, ``nsvc``, or ``component``
- [pipeline-state-reporter] state version of the generated message

Fixed
~~~~~

- [ansible] to process ``--ansible-playbook-options``, use gluetool's ``normalize_multistring_option``


2018-10-15
----------

Changed
~~~~~~~

- [koji-fedora] when build is available, extract source from it, otherwise task's ``request`` field is used
- [restraint-scheduler] guest provisioning and setup are completely paralelized
- [restraint-scheduler] check and report progress of provisioning/guest setup as soon as possible
- [rules-engine] context is now logged using ``verbose`` severity
- [wow] "No test available" error will not be reported to Sentry anymore

Added
~~~~~

- Optional type check job in Gitlab CI
- [beaker-jobwatch] allow caller disable live streaming of ``beaker-jobwatch`` output
- [beaker-provisioner] when provisioning, log the requested testing environment
- [beaker-provisioner] support the real provisioning of guests ("dynamic" guests, as oposed to "static" ones already supported)
- [bkr] access to job results
- [bkr] matrix URL parser
- [build-dependencies] support for companions from Copr
- [mbs] new module - experimental support for Module Building Service (future ``redhat-module`` artifacts)
- [memcached] new module - access to Memcached cache API
- [openstack] when provisioning, log the requested testing environment
- [openstack] when guests are provisioned, log them with INFO level to display their properties, namely their IP addresses
- [pipeline-state-reporter] publish value of ``--label`` in eval context
- [restraint-scheduler] guest provisioning and setup are completely paralelized
- [restraint-scheduler] check and report progress of provisioning/guest setup as soon as possible
- [restraint-scheduler] "No testable artifacts error" gained access to supported arches, providing more descriptive e-mail notification
- [rules-engine] allow creation of dictionaries in rules
- [wow] user of ``beaker_job_xml`` can now force use of a specific distro

Removed
~~~~~~~

- [beaker-jobwatch] don't log the last line of ``beaker-jobwatch`` output, module has its own messages
- [test-batch-planner] disable warning on match not being equal to the component

Fixed
~~~~~

- [beaker-provisioner] avoid using ``message`` attribute of an exception, it has been deprecated for ``BaseException`` and its children
- [brew] don't raise error when asked for eval context before ``execute`` gets called
- [build-on-commit] better handling of situation when the remote branch has been removed from the repository
- [copr] better check for possible missing build task info in Copr API
- [copr] adds NVR check after artifact installation
- [copr] don't raise error when asked for eval context before ``execute`` gets called
- [mbs] don't raise error when asked for eval context before ``execute`` gets called
- [restraint-scheduler] apply ``decode()`` on distro name and architecture when extracting them from recipe XML
- [restraint-scheduler] "No testable artifacts error" gained access to supported arches, providing more descriptive e-mail notification


2018-09-19
----------

Changed
~~~~~~~

- Versions of several required Python packages were bumped to match the most recent Gluetool release
- [copr] refactored internal use of Copr API
- [covscan] refactored to be less tied to Brew, allowing the use with other artifact providers like Copr
- [restraint-scheduler] flow of guest provisioning and setup process has been changed to setup all provisioned guests - for all jobs and recipes - in parallel


Added
~~~~~

- Re-enabled Ansible Tower integration
- [ansible] it is now possible to provide additional options to be given to Ansible when running playbooks (``--ansible-playbook-options``)
- [ansible] custom exception wrapping Ansible errors
- [beaker-job-xml] new module - allow the use of static XML describing Beaker jobs
- [bkr] new module - wrapper of (low-level) Beaker API and commands (e.g. ``bkr job-submit``)
- [install-koji-docker-image] export PHASE=artifact-installation variable to Beaker XML provider
- [notify-email] when formatting an error e-mail, body header and footer now have access to a Failure instance
- [notify-email] SMTP port is now configurable (``--smtp-port``)


Fixed
~~~~~

- [beaker-provisioner] when provisioning guests, honor testing environment architecture specified by a requestor
- [copr] even incomplete information about the task can be now used in error handling process
- [openstack] when creating an instance, multiple images of the same name are now handled correctly
- [openstack] fixed removal of inactive images
- [pipeline-state-reporter] fixed processing of ``--dont-report-running`` option
- [test-batch-planner] safer handling of regular expressions made of a component name when searching component tasks
