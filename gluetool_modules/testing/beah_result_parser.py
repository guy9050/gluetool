import datetime

import gluetool
from gluetool.log import log_blob
from gluetool.utils import treat_url


class BeahResultParser(gluetool.Module):
    """
    Provides common processor for XML documents, returned by Beaker and Restraint. Using these data,
    it returns `result`, a dictionary with "standardized" keys, helping its callers to deal
    with differencies between different XML providers.
    """

    name = 'beah-result-parser'
    description = 'Processing of XML results provided by Beaker or Restraint.'

    shared_functions = ('parse_beah_result',)

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    def _find_architecture(self, result, task, journal, recipe):
        # Architecture is not in <task/> but journal and recipe seem to be a reliable sources

        # pylint: disable=unused-argument

        if recipe and 'arch' in recipe.attrs:
            result['bkr_arch'] = recipe['arch']

        elif journal and journal.arch:
            result['bkr_arch'] = journal.arch.string.strip()

        else:
            self.debug('Cannot deduce architecture')

    def _find_connectable_host(self, result, connectable_hostname):
        # Connectable hostname

        # pylint: disable=no-self-use

        if connectable_hostname is not None:
            result['connectable_host'] = connectable_hostname

        else:
            result['connectable_host'] = result['bkr_host']

    def _find_distro(self, result, task, journal, recipe):
        # Distro

        # pylint: disable=unused-argument

        if recipe and 'distro' in recipe.attrs:
            result['bkr_distro'] = recipe['distro']

        else:
            self.debug('Cannot deduce recipe distro')

    def _find_duration(self, result, task, journal, recipe):
        # Task duration - Beaker provides this info in <task/>, restraint does not but it's possible
        # it stores the data into journal

        # pylint: disable=unused-argument

        if task and task.has_attr('duration'):
            duration = 0

            days_and_hours = task['duration'].split(',')  # "1 day, 23:51:43"

            if len(days_and_hours) > 1:
                duration += int(days_and_hours.pop(0).split(' ')[0]) * 86400

            # Walk chunks in the reversed order, from seconds to hours - it is
            # possible there's just a single chunk (seconds), or two (seconds
            # and minutes), and accessing all three of them could lead to IndexError.
            # And multiply each chunk with the corresponding coefficient.
            coefficients = [1, 60, 3600]

            _chunks = [int(_chunk) for _chunk in days_and_hours[0].split(':')]

            for chunk in reversed(_chunks):
                duration += (chunk * coefficients.pop(0))

            result['bkr_duration'] = duration

        elif journal and journal.starttime and journal.endtime:
            starttime = ' '.join(journal.starttime.string.strip().split(' ')[0:-1])
            endtime = ' '.join(journal.endtime.string.strip().split(' ')[0:-1])

            started = datetime.datetime.strptime(starttime, '%Y-%m-%d %H:%M:%S')
            ended = datetime.datetime.strptime(endtime, '%Y-%m-%d %H:%M:%S')

            result['bkr_duration'] = int((ended - started).total_seconds())

        else:
            self.debug('Cannot deduce task duration')

    def _find_machine(self, result, task, journal, recipe):
        # Machine the task ran on

        # pylint: disable=unused-argument

        if task and task.roles and task.roles.find_all('system'):
            result['bkr_host'] = task.roles.find_all('system')[0]['value']

        elif journal and journal.hostname:
            result['bkr_host'] = journal.hostname.string.strip()

        else:
            self.debug('Cannot deduce hostname')

    def _find_packages(self, result, task, journal, recipe):
        # Packages - sometimes they are listed, sometimes not, but always in journal

        # pylint: disable=unused-argument

        if journal:
            packages = {}

            for pkgdetails in journal.find_all('pkgdetails'):
                if pkgdetails.has_attr('sourcerpm'):
                    packages[pkgdetails['sourcerpm']] = True

                packages[pkgdetails.string.strip()] = True

            result['bkr_packages'] = [k.strip() for k in sorted(packages.keys())]

        else:
            self.debug('Cannot deduce involved packages')

    def _find_params(self, result, task, journal, recipe):
        # Task params are just in <task/>

        # pylint: disable=unused-argument

        if task and task.params:
            result['bkr_params'] = [
                '{}=\"{}\"'.format(param['name'], param['value']) for param in task.params.find_all('param')
            ]

        else:
            self.debug('Cannot deduce task parameters')

    def _find_recipe_id(self, result, task, journal, recipe):
        # Recipe ID is only in recipe

        # pylint: disable=unused-argument

        if recipe and 'id' in recipe.attrs:
            result['bkr_recipe_id'] = int(recipe['id'])

        else:
            self.debug('Cannot deduce recipe ID')

    def _find_variant(self, result, task, journal, recipe):
        # Distro variant is only in recipe

        # pylint: disable=unused-argument

        if recipe and 'variant' in recipe.attrs:
            result['bkr_variant'] = recipe['variant']

        else:
            self.debug('Cannot deduce recipe variant')

    def _find_version(self, result, task, journal, recipe):
        # Version - restraint does not export this

        # pylint: disable=unused-argument

        if task and task.has_attr('version'):
            result['bkr_version'] = task['version']

        else:
            self.warn('Cannot deduce bkr version')

    def _find_logs(self, root, artifact_path):
        # pylint: disable=no-self-use
        if root.logs is None:
            return []

        logs = root.logs.find_all('log')
        if not logs:
            return []

        if not artifact_path:
            def _artifact_path_nop(s):
                return s

            artifact_path = _artifact_path_nop

        name_attr = 'name' if 'name' in logs[0].attrs else 'filename'
        path_attr = 'href' if 'href' in logs[0].attrs else 'path'

        return [
            {
                'name': str(log[name_attr]),
                'href': treat_url(artifact_path(log[path_attr]))
            } for log in logs
        ]

    def _find_phases(self, result, task, journal, artifact_path):
        # Task phases are in <task/>, or, lacking some information, in journal

        if task and task.results:
            result['bkr_phases'] = [
                {
                    'name': phase['path'],
                    'result': phase['result'],
                    'logs': self._find_logs(phase, artifact_path)
                } for phase in task.results.find_all('result')
            ]

        elif journal and journal.log:
            result['bkr_phases'] = [
                {
                    'name': phase['name'],
                    'result': phase['result'],
                    'logs': self._find_logs(phase, artifact_path)
                } for phase in journal.log.find_all('phase')
            ]

        else:
            self.warn('Cannot deduce task phases')

    def parse_beah_result(self, task, journal=None, recipe=None, artifact_path=None, connectable_hostname=None):
        # pylint: disable=line-too-long
        """
        Processes XML description of task result, complemented by the recipe XML and journal, if available,
        and returns dictionary with "standardized" keys, describing the result.


        Following blocks describe building blocks of the returned dictionary.


        **Artifact location** describes a location (URL) of task artifact - log, journal, attached files, etc. It has
        the following properties:

        - ``name`` (string): name of the artifact as reported in the task results.
        - ``href`` (string): URL of the artifact.

        .. code-block:: json

           {
             "href": "https://some.jenkins.master.com/job/some-job/1098/artifact/tmpAM1MY5.01/recipes/1/tasks/2/logs/harness.log",
             "name": "harness.log"
           }


        **Task phase** describes a single phase of a task. It has the following properties:

        * ``logs`` (list): list of `Artifact location` items, listing all logs related to the phase.
        * ``name`` (string): name of the phase.
        * ``result`` (string): result of the task, usually ``PASS`` or ``FAIL``, but other values may appear as well.

        .. code-block:: json

           {
               "logs": [
                   {
                       "href": "https://some.jenkins.master.com/job/some-job/artifact/././tmpnMUPJa.01/recipes/1/tasks/4/results/1498637705/logs/dmesg.log",
                       "name": "dmesg.log"
                   },
                   {
                       "href": "https://some.jenkins.master.com/job/some-job/artifact/././tmpnMUPJa.01/recipes/1/tasks/4/results/1498637705/logs/avc.log",
                       "name": "avc.log"
                   },
                   {
                       "href": "https://some.jenkins.master.com/job/some-job/artifact/././tmpnMUPJa.01/recipes/1/tasks/4/results/1498637705/logs/resultoutputfile.log",
                       "name": "resultoutputfile.log"
                   }
               ],
               "name": "Setup",
               "result": "PASS"
           }


        **Result** is a description of task result as returned by ``parse_beah_result``. Pretty much every field
        is optional - keys are always present in the result but the value may be ``null`` or an empty ``dict``
        or ``list``. It has the following properties:

        * ``bkr_arch`` (string): architecture the task ran on, e.g. ``x86_64``.
        * ``bkr_distro`` (string): distribution installed on the machine, e.g. ``RHEL-7.3``.
        * ``bkr_duration`` (number): duration fo the task, in seconds.
        * ``bkr_host`` (string): hostname of the machine the task ran on, e.g. ``foo.bar.com``.
        * ``bkr_logs`` (list): list of `Artifact location` instances, listing task artifacts.
        * ``bkr_packages`` (list): list of strings, listing NVR of packages that were, for some reason,
            considered interesting by the task.
        * ``bkr_params`` (list): list of strings, in form of ``<NAME>="<VALUE>"``, listing task parameters
            passed to the task, e.g. ``BEAKERLIB_RPM_DOWNLOAD_METHODS="yum direct"``.
        * ``bkr_phases`` (list): list of `Task phase` instances, describing each phase of the task,
            in the order they were executed.
        * ``bkr_recipe_id`` (integer): ID of the recipe that contained this task.
        * ``bkr_result`` (string): result of the task, usually ``PASS`` or ``FAIL``, but other values may
            appear as well.
        * ``bkr_status`` (string): status of the task, e.g. ``New``, ``Processed``, ``Scheduled``, or, in the case
            of completed task, ``Completed``.
        * ``bkr_task_id`` (integer): ID of the task.
        * ``bkr_variant`` (string): distro variant, e.g. ``Server``.
        * ``connectable_host`` (string): hostname user should use when attempting to connect to the machine, e.g. over ssh.
        * ``name`` (string): task name.

        .. code-block:: json

           {
               "bkr_arch": "x86_64",
               "bkr_distro": null,
               "bkr_duration": 0.0,
               "bkr_host": "foo.bar.com",
               "bkr_logs": [
                   {
                       "href": "https://some.jenkins.master.com/job/some-job/1098/artifact/tmpAM1MY5.01/recipes/1/tasks/2/logs/harness.log",
                       "name": "harness.log"
                   }
               ],
               "bkr_packages": [
                   "package-1-3.11.el7.src.rpm",
                   "package-1-3.11.8-7.el7.x86_64"
               ],
               "bkr_params": [
                   "BEAKERLIB_RPM_DOWNLOAD_METHODS='yum direct'"
               ],
               "bkr_phases": [
                   {
                       "logs": [
                           {
                               "href": "https://some.jenkins.master.com/job/some-job/artifact/././tmpnMUPJa.01/recipes/1/tasks/4/results/1498637705/logs/dmesg.log",
                               "name": "dmesg.log"
                           },
                           {
                               "href": "https://some.jenkins.master.com/job/some-job/artifact/././tmpnMUPJa.01/recipes/1/tasks/4/results/1498637705/logs/avc.log",
                               "name": "avc.log"
                           },
                           {
                               "href": "https://some.jenkins.master.com/job/some-job/artifact/././tmpnMUPJa.01/recipes/1/tasks/4/results/1498637705/logs/resultoutputfile.log",
                               "name": "resultoutputfile.log"
                           }
                       ],
                       "name": "Setup",
                       "result": "PASS"
                   }
               ],
               "bkr_recipe_id": null,
               "bkr_result": "PASS",
               "bkr_status": "Completed",
               "bkr_task_id": "3",
               "bkr_variant": null,
               "bkr_version": null,
               "connectable_host": "10.11.12.13",
               "name": "/some/beaker/task"
           }


        :param element task: XML describing task result, usualy produced by Beaker or Restraint job.
            It is the first "source of truth".
        :param element journal: XML representing task journal. Optional source of information, may
            provide additional bits.
        :param element recipe: XML representing the recipe including the task prescription. Optional
            source of information, may provide additional bits.
        :param callable artifact_path: used to treat URL of every artifact mentioned in the final
            result. Accepts a raw URL (``str``) and returns the "cleansed" version. If not set,
            the original, raw URLs are used.
        :param str connectable_hostname: if set, it specifies value of ``connectable_host`` key. Otherwise,
            function tries to deduce the hostname from provided sources. Be aware that for some hostnames,
            the hostname may not be accessible - in that case, caller should specify the correct
            hostname via ``connectable_hostname`` parameter.
        :returns: ``dict`` describing the result.
        """

        # pylint: disable=too-many-arguments,too-many-branches,too-many-statements

        log_blob(self.debug, 'task XML', task.prettify(encoding='utf-8'))

        if journal:
            log_blob(self.debug, 'task journal', journal.prettify(encoding='utf-8'))
        else:
            self.debug('task journal not specified')

        if recipe:
            log_blob(self.debug, 'recipe XML', recipe.prettify(encoding='utf-8'))
        else:
            self.debug('recipe not specified')

        # Initialize result dictionary
        result = {
            'name': task['name'],
            'bkr_arch': None,
            'bkr_distro': None,
            'bkr_variant': None,
            'bkr_duration': 0,
            'bkr_host': None,
            'connectable_host': None,
            'bkr_logs': self._find_logs(task, artifact_path),
            'bkr_packages': [],
            'bkr_params': [],
            'bkr_phases': [],
            'bkr_recipe_id': None,
            'bkr_result': task['result'],
            'bkr_status': task['status'],
            'bkr_task_id': int(task['id']),
            'bkr_version': None
        }

        # Now fill in blank spaces, if possible
        #
        # Following pieces could be rearranged to put together keys that can be
        # read from the same source, if the source exists, but I'd like to keep
        # them organized per key - for a single piece of information, try the first
        # source, then another, then another, ... It leads to duplicities wrt. test
        # whether e.g. journal is set, but it's better structured wrt. what key comes
        # from which source.

        for bit in ('architecture', 'distro', 'duration', 'machine', 'packages', 'params', 'recipe_id', 'variant',
                    'version'):
            getattr(self, '_find_{}'.format(bit))(result, task, journal, recipe)

        self._find_phases(result, task, journal, artifact_path)
        self._find_connectable_host(result, connectable_hostname)

        return result
