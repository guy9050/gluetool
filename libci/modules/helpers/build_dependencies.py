import libci
from libci.log import log_dict


class BuildDependencies(libci.Module):
    """
    Tested packages may have additional dependencies: "I'd like CI to install additional builds
    when testing package X". This module tries to solve this use case, providing different methods
    of lookup of these dependencies, and extends the list of tasks pipeline runs for.

    Following methods are available:

    * ``companions-from-koji``: takes a list of `companions` (via ``--companions`` option), tries
      to lookup up the latest possible builds for them, respecting the build target.

    .. warning::

       This module is still under development. Its API and options may change as necessary.
    """

    name = 'build-dependencies'
    description = 'Finds (and adds) possible build dependencies.'

    options = {
        'method': {
            'help': 'What method to use for dependencies lookup.',
            'choices': ('companions-from-koji',),
            'default': None,
            'metavar': 'METHOD'
        },
        'companions': {
            'help': 'List of additional components to look for.',
            'action': 'append',
            'default': [],
            'metavar': 'COMPONENT1,...'
        },
        'companion-target-fallback-map': {
            'help': 'When there is not build for given build target, try another target as well.',
            'metavar': 'FILE',
            'default': None
        }
    }

    @libci.utils.cached_property
    def companion_target_fallback_map(self):
        if not self.option('companion-target-fallback-map'):
            return None

        return libci.utils.PatternMap(self.option('companion-target-fallback-map'), logger=self.logger)

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

                try:
                    alternative_target = self.companion_target_fallback_map.match(target)

                except libci.CIError as exc:
                    self.warn("Cannot fall back from a target '{}'".format(target), sentry=True)
                    self.warn("No builds found for component '{}' and target '{}'".format(component, target))
                    return None

                return self._find_task_for_target_and_component(session, alternative_target, component)

            raise exc

        log_dict(self.debug, 'found builds', builds)

        if not builds:
            self.warn("No builds found for component '{}' and target '{}'".format(component, target))
            return None

        matching_builds = [build for build in builds if build['tag_name'] == target]
        log_dict(self.debug, 'matching builds', matching_builds)

        if not matching_builds:
            self.warn("No matching builds found for component '{}' and target '{}'".format(component, target))
            return None

        return int(matching_builds[0]['task_id'])

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

        # merge all lists of companions, and separate each component
        companions = sum([[s.strip() for s in companions.split(',')] for companions in self.option('companions')], [])

        self.info('Looking for companions {}'.format(', '.join(companions)))

        task_ids = [
            # pylint: disable=line-too-long
            self._find_task_for_target_and_component(session, primary_task.target, companion) for companion in companions  # Ignore PEP8Bear
        ]

        # Filter out only the real task IDs, ignore "not found" represented by None
        real_task_ids = [task_id for task_id in task_ids if task_id is not None]

        log_dict(self.debug, 'found task ids', real_task_ids)

        return real_task_ids

    _methods = {
        'companions-from-koji': _companions_from_koji
    }

    def sanity(self):
        if self.option('method') == 'companions-from-koji' and not self.option('companions'):
            # pylint: disable=line-too-long
            raise libci.utils.IncompatibleOptionsError(self, "--companions option is required with method 'companions-from-koji'")  # Ignore PEP8Bear

    def execute(self):
        self.require_shared('tasks')

        if self.option('method') is None:
            self.info('No method specified, moving on.')
            return

        method = self._methods.get(self.option('method'), None)

        if method is None:
            # pylint: disable=line-too-long
            raise libci.utils.IncompatibleOptionsError(self, "Unknown 'guessing' method '{}'".format(self.option('method')))  # Ignore PEP8Bear

        additional_task_ids = method(self)

        if additional_task_ids:
            current_tasks_ids = [task.task_id for task in self.shared('tasks')]

            log_dict(self.debug, 'current task IDs', current_tasks_ids)
            log_dict(self.debug, 'additional task IDs', additional_task_ids)

            self.shared('tasks', task_ids=current_tasks_ids + additional_task_ids)

        log_dict(self.info, 'Updated list of tasks', [task.full_name for task in self.shared('tasks')])