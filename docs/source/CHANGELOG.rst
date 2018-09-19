Changelog
=========

All notable changes to this project will be documented in this file.

The format is based on `Keep a Changelog <https://keepachangelog.com/en/1.0.0/>`_.

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
