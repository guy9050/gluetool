import gluetool
from gluetool.log import log_dict
from gluetool.utils import normalize_multistring_option


class BuildDependencies(gluetool.Module):
    """
    Tested packages may have additional dependencies: "I'd like CI to install additional builds
    when testing package X". This module tries to solve this use case, providing different methods
    of lookup of these dependencies, and extends the list of tasks pipeline runs for.

    Following methods are available:

    * ``companions-from-koji``: takes a list of `companions` (via ``--companions`` option), tries
      to lookup up the latest possible builds for them, respecting the build target.

    * ``companions-from-copr``: takes a list of `companions` (via ``--companions`` option), tries
      to lookup their latest build with same build target in project, which primary task belongs to.

    .. warning::

       This module is still under development. Its API and options may change as necessary.
    """

    name = 'build-dependencies'
    description = 'Finds (and adds) possible build dependencies.'

    options = {
        'method': {
            'help': 'What method to use for dependencies lookup (default: %(default)s).',
            'choices': ('companions-from-koji', 'companions-from-copr'),
            'default': None,
            'metavar': 'METHOD'
        },
        'companions': {
            'help': 'List of additional components to look for (default: none).',
            'action': 'append',
            'default': [],
            'metavar': 'COMPONENT1,...'
        },
        'companion-target-fallback-map': {
            'help': """
                    When there is not build for given build target, try another target as well
                    (default: %(default)s).
                    """,
            'metavar': 'FILE',
            'default': None
        }
    }

    def __init__(self, *args, **kwargs):
        super(BuildDependencies, self).__init__(*args, **kwargs)
        self.companions = None

    @gluetool.utils.cached_property
    def companion_target_fallback_map(self):
        if not self.option('companion-target-fallback-map'):
            return None

        return gluetool.utils.PatternMap(self.option('companion-target-fallback-map'), logger=self.logger)

    def _find_task_for_target_and_component(self, session, target, component):
        """
        Find the most recent task ID for given component and build target.

        .. warning::

           The search **does not** see scratch builds.

        :param session: Remote API session.
        :param str target: Build target.
        :param str component: Component name.
        :rtype: int
        :returns: Task ID, or ``None`` if there is no matching task.
        """

        self.debug("looking for builds of component '{}' with target '{}'".format(component, target))

        import koji

        try:
            builds = session.getLatestBuilds(target, package=component)

        except koji.GenericError as exc:
            # Some targets exist in multiple versions, mixing lower- and upper case. Deal with it.
            # We're giving our users chance to use another target and try again.
            if exc.message == 'No such entry in table tag: {}'.format(target):
                if self.companion_target_fallback_map is None:
                    self.warn("No companion target map set, cannot fall back from '{}'".format(target), sentry=True)
                    self.warn("No builds found for component '{}' and target '{}'".format(component, target))
                    return None

                self.debug('No build found, try to fall back')

                try:
                    alternative_target = self.companion_target_fallback_map.match(target)

                except gluetool.GlueError as exc:
                    self.warn("Cannot fall back from a target '{}'".format(target), sentry=True)
                    self.warn("No builds found for component '{}' and target '{}'".format(component, target))
                    return None

                return self._find_task_for_target_and_component(session, alternative_target, component)

            raise exc

        log_dict(self.debug, 'found builds', builds)

        if not builds:
            self.warn("No builds found for component '{}' and target '{}'".format(component, target))
            return None

        return int(builds[0]['task_id'])

    def _companions_from_koji(self):
        """
        Probably the simplest dynamic method: look for the most recent build for each companion,
        with the matching build target.

        :rtype: list(int)
        :returns: List of task IDs found for companions.
        """

        self.require_shared('koji_session', 'primary_task')

        session = self.shared('koji_session')
        primary_task = self.shared('primary_task')

        self.info('Looking for companions {}'.format(', '.join(self.companions)))

        task_ids = [
            # pylint: disable=line-too-long
            self._find_task_for_target_and_component(session, primary_task.target, companion) for companion in self.companions  # Ignore PEP8Bear
        ]

        # Filter out only the real task IDs, ignore "not found" represented by None
        real_task_ids = [task_id for task_id in task_ids if task_id is not None]

        log_dict(self.debug, 'found task ids', real_task_ids)

        return real_task_ids

    def _companions_from_copr(self):

        self.require_shared('copr_api', 'primary_task')

        companions_ids = []
        found_companions = []

        copr_api = self.shared('copr_api')
        task = self.shared('primary_task')

        self.info('Looking for companions {}'.format(', '.join(self.companions)))

        project_href = copr_api.get_build_info(task.task_id.build_id)['_links']['project']['href']

        builds_href = copr_api.get_href(project_href)['_links']['builds']['href']

        for build in copr_api.get_href(builds_href)['builds']:
            package_name = build['build']['package_name']
            if package_name in self.companions:
                build_id = build['build']['id']
                build_tasks_href = build['_links']['build_tasks']['href']

                for build_task in copr_api.get_href(build_tasks_href)['build_tasks']:
                    if build_task['build_task']['chroot_name'] == task.task_id.chroot_name:
                        chroot_name = build_task['build_task']['chroot_name']

                        companions_ids.append('{}:{}'.format(build_id, chroot_name))
                        found_companions.append(package_name)
                        self.debug('{} bound - {}:{}'.format(build, build_id, chroot_name))

        if len(self.companions) != len(found_companions):
            self.warn('Number of found companions are not equal to required one!', sentry=True)
            self.warn('Required: {}'.format(self.companions))
            self.warn('Found: {}'.format(found_companions))

        return companions_ids

    _methods = {
        'companions-from-koji': _companions_from_koji,
        'companions-from-copr': _companions_from_copr
    }

    def sanity(self):
        method = self.option('method')

        self.companions = normalize_multistring_option(self.option('companions'))

        if self.companions and not method:
            raise gluetool.utils.IncompatibleOptionsError('--companions option specified but no --method selected')

        if method in self._methods.keys() and not self.companions:
            # pylint: disable=line-too-long
            raise gluetool.utils.IncompatibleOptionsError("--companions option is required with methods: {}.".format(', '.join(self._methods.keys())))  # Ignore PEP8Bear

    def execute(self):
        self.require_shared('primary_task', 'tasks')

        if self.option('method') is None:
            self.info('No method specified, moving on.')
            return

        # It may happen that user configured CI to run a single command for mulptiple components, adding them
        # as each others companions as well, e.g. "for A, B or C, run foo and, as companions, install latest
        # builds of A, B and C". In such case, we'd try to install A's build under the test and the latest
        # regular build of A at the same moment, and these two build may be different builds (think scratch build,
        # newer than the most recent regular build). Our attempt to install these two builds of component A
        # would obviously fail. To avoid that situation, if the primary component is present on the list
        # of companions, remove it, that way we would just try to install A's build under the test.
        primary_component = self.shared('primary_tasks').component
        if primary_component in self.companions:
            self.info("removing primary component '{}' from a list of companions".format(primary_component))

            self.companions.remove(primary_component)

        log_dict(self.debug, 'final list of companions', self.companions)

        method = self._methods.get(self.option('method'), None)

        if method is None:
            # pylint: disable=line-too-long
            raise gluetool.utils.IncompatibleOptionsError("Unknown 'guessing' method '{}'".format(self.option('method')))  # Ignore PEP8Bear

        additional_task_ids = method(self)

        if additional_task_ids:
            current_tasks_ids = [task.id for task in self.shared('tasks')]

            log_dict(self.debug, 'current task IDs', current_tasks_ids)
            log_dict(self.debug, 'additional task IDs', additional_task_ids)

            self.shared('tasks', task_ids=current_tasks_ids + additional_task_ids)

        log_dict(self.info, 'Updated list of tasks', [task.full_name for task in self.shared('tasks')])
