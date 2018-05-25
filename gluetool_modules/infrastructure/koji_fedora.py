# pylint: disable=too-many-lines

import collections
import re
import koji

from bs4 import BeautifulSoup
from rpmUtils.miscutils import splitFilename

import gluetool
from gluetool import GlueError, SoftGlueError
from gluetool.log import Logging, log_dict
from gluetool.utils import cached_property, dict_update, fetch_url, wait, normalize_multistring_option


class NotBuildTaskError(SoftGlueError):
    def __init__(self, task_id):
        super(NotBuildTaskError, self).__init__('Task is not a build task')

        self.task_id = task_id


class NoArtifactsError(SoftGlueError):
    def __init__(self, task_id):
        super(NoArtifactsError, self).__init__('No artifacts found for task')

        self.task_id = task_id


#: Information about task architectures.
#:
#: :ivar bool complete: If ``True``, the task was not limited by its issuer to any particular set of architectures.
#:     ``False`` signals the issuer requested task to build its artifact for specific list of architectures.
#: :ivar list(str) arches: List of architectures.
TaskArches = collections.namedtuple('TaskArches', ['complete', 'arches'])

#: Represents ``request`` field of API response on ``getTaskInfo`` query for common build task.
#:
#: :ivar str source: source used for the building process.
#: :ivar str target: target the task built for.
#: :ivar dict options: additional task options.
BuildTaskRequest = collections.namedtuple('BuildTaskRequest', ['source', 'target', 'options'])

#: Represents ``request`` field of API response on ``getTaskInfo`` query for ``buildArch`` task.
#:
#: :ivar str source: source used for the building process.
#: :ivar something: some value of unknown purpose.
#: :ivar str arch: build architecture.
#: :ivar bool keep_srpm: whether the SRPM was stored among artifacts.
#: :ivar dict options: additional task options.
BuildArchTaskRequest = collections.namedtuple('BuildArchTaskRequest',
                                              ['source', 'something', 'arch', 'keep_srpm', 'options'])


class KojiTask(object):
    # pylint: disable=too-many-public-methods

    """
    Provides abstraction of a koji build task, specified by task ID. For initialization
    koji instance details need to be passed via the instance dictionary with the following keys:

        ``session`` - a koji session initialized via the koji.ClientSession function
        ``url`` - a base URL for the koji instance
        ``pkgs_url`` - a base URL for the packages location

    :param dict details: Instance details, see ``required_instance_keys``
    :param int task_id: Initialize from given Koji task ID.
    :param module: Module that created this task instance.
    :param gluetool.log.ContextLogger logger: logger used for logging
    :param bool wait_timeout: Wait for task to become non-waiting

    :ivar int id: unique ID of the task on the Koji instance.
    """

    ARTIFACT_NAMESPACE = 'koji-build'

    @staticmethod
    def _check_required_instance_keys(details):
        """
        Checks for required instance details for Koji.
        :raises: GlueError if instance is missing some of the required keys
        """
        required_instance_keys = ('session', 'url', 'pkgs_url', 'web_url')

        if not all(key in details for key in required_instance_keys):
            raise GlueError('instance details do not contain all required keys')

    # pylint: disable=too-many-arguments
    def __init__(self, details, task_id, module, logger=None, wait_timeout=None):
        self._check_required_instance_keys(details)

        self.logger = logger or Logging.get_logger()
        logger.connect(self)

        self._module = module

        # pylint: disable=invalid-name
        self.id = int(task_id)
        self.api_url = details['url']
        self.web_url = details['web_url']
        self.pkgs_url = details['pkgs_url']
        self.session = details['session']

        # first check if the task is valid for our case
        if not self._is_valid:
            raise NotBuildTaskError(self.id)

        # wait for the task to be non-waiting and closed
        wait('waiting for task to be non waiting', self._check_nonwaiting_task, timeout=wait_timeout)

        # wait for task to be in CLOSED state
        # note that this can take some amount of time after it becomes non-waiting
        wait('waiting for task to be closed', self._check_closed_task, timeout=wait_timeout)

    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, self.id)

    @cached_property
    def _is_valid(self):
        """
        Verify the task is valid by checking its ``method`` attribute. List of values that are considered
        `valid` is provided by the user via ``--valid-methods`` option of the module, and generaly limits
        what tasks the pipeline deals with, e.g. it is designed to run tests on Docker images, therefore
        disallows any other method than ``buildContainer``. If there is no specific list of valid methods,
        all methods are considered valid.

        :rtype: bool
        """

        # pylint: disable=protected-access

        if not self._module._valid_methods:
            return True

        return self._task_info['method'] in self._module._valid_methods

    def _flush_task_info(self):
        """
        Remove cached task info we got from API. Handle the case when such info does not yet exist.
        """

        try:
            del self._task_info

        except AttributeError:
            pass

    def _check_closed_task(self):
        """
        Verify that the task is closed.

        :returns: True if task is closed, False otherwise
        """

        self._flush_task_info()

        return self._task_info['state'] == koji.TASK_STATES['CLOSED']

    def _check_nonwaiting_task(self):
        """
        Check if task is non-waiting, i.e. 'waiting: false' in task info.
        :returns: True if task is non-waiting, False otherwise
        """

        self._flush_task_info()

        return self._task_info['waiting'] is not True

    @cached_property
    def _subtasks(self):
        """
        A list of children tasks in raw form, as JSON data returned by Koji API.

        :rtype: list(dict)
        """

        subtasks = self.session.getTaskChildren(self.id, request=True)
        log_dict(self.debug, 'subtasks', subtasks)

        return subtasks

    @cached_property
    def _build_arch_subtasks(self):
        """
        A list of children task of ``buildArch`` type, as JSON data returned by Koji API.

        :rtype: list(dict)
        """

        subtasks = [task for task in self._subtasks if task['method'] == 'buildArch']

        log_dict(self.debug, 'buildArch subtasks', subtasks)

        for task in subtasks:
            KojiTask.swap_request_info(task, BuildArchTaskRequest, 5)

        return subtasks

    @staticmethod
    def swap_request_info(task_info, klass, nr_fields):
        """
        Replace ``request`` key of task info - a JSON structure, returned by API - with
        an object with properties, representing the content of ``request`` key.
        """

        request_info = task_info.get('request', None)

        if request_info is None:
            raise GlueError("Task {} has no request field in task info".format(task_info['id']))

        if len(request_info) < nr_fields:
            raise GlueError("Task {} has unexpected number of items in request field".format(task_info['id']))

        task_info['request'] = klass(*[request_info[i] for i in range(0, nr_fields)])

    @cached_property
    def _task_info(self):
        """
        Task info as returned by API.

        :rtype: dict
        """

        task_info = self.session.getTaskInfo(self.id, request=True)

        if not task_info:
            raise GlueError("Task '{}' not found".format(self.id))

        log_dict(self.debug, 'task info', task_info)

        KojiTask.swap_request_info(task_info, BuildTaskRequest, 3)

        return task_info

    @cached_property
    def _build(self):
        """
        Build info as returned by API, or ``None`` for scratch builds.

        :rtype: dict
        """

        if self.scratch:
            return None

        builds = self.session.listBuilds(taskID=self.id)
        log_dict(self.debug, 'builds for task ID {}'.format(self.id), builds)

        if not builds:
            return None

        return builds[0]

    @cached_property
    def _result(self):
        """
        Task result info as returned by API.

        :rtype: dict
        """

        result = self.session.getTaskResult(self.id)

        log_dict(self.debug, 'task result', result)

        return result

    @cached_property
    def _task_request(self):
        return self._task_info['request']

    @cached_property
    def has_build(self):
        """
        Whether there is a build for this task.

        If there is a ``self.build_id``, then we have a build. ``self.build_id`` is extracted from ``self._build``,
        therefore we can inject ``self._build`` - like Brew's ``buildContainer`` tasks do - and this will work
        like a charm.
        """

        return self.build_id is not None

    @cached_property
    def is_build_task(self):
        """
        Whether this task is a "build" task, i.e. building common RPMs.
        """

        return self._task_info['method'] == 'build'

    @cached_property
    def build_id(self):
        """
        Build ID for standard tasks, or ``None`` for scratch builds.

        :rtype: int
        """

        if not self._build:
            return None

        return self._build['build_id']

    @cached_property
    def owner(self):
        """
        Name of the owner of the task.

        :rtype: str
        """

        owner_id = self._task_info["owner"]
        return self.session.getUser(owner_id)["name"]

    @cached_property
    def issuer(self):
        """
        Name of the issuer of the task. The same as :py:attr:`owner`.

        :rtype: str
        """

        return self.owner

    @cached_property
    def target(self):
        """
        Build target name

        :rtype: str
        """

        if self._task_request.target:
            return self._task_request.target

        # inform admins about this weird build
        self.warn("task '{}' build '{}' has no build target".format(self.id, self.nvr), sentry=True)

        return '<no build target available>'

    @cached_property
    def source(self):
        """
        Task's source, e.g. git+https://src.fedoraproject.org/rpms/rust-tokio-proto.git?#b59219

        :rtype: str
        """

        if self._task_request.source:
            return self._task_request.source

        raise GlueError("task '{}' has no source defined in the request field".format(self.id))

    @cached_property
    def scratch(self):
        """
        Whether the task is a scratch build.

        :rtype: bool
        """

        return self._task_request.options.get('scratch', False)

    @cached_property
    def task_arches(self):
        """
        Return information about arches the task was building for.

        :rtype: TaskArches
        """

        arches = self._task_request.options.get('arch_override', None)

        if arches is not None:
            return TaskArches(False, [arch.strip() for arch in arches.split(' ')])

        return TaskArches(True, [child['arch'] for child in self._build_arch_subtasks])

    @cached_property
    def url(self):
        """
        URL of the task info web page.

        :rtype: str
        """

        return "{}/taskinfo?taskID={}".format(self.web_url, self.id)

    @cached_property
    def latest(self):
        """
        NVR of the latest released package with the same destination tag, or ``None`` if none found.

        :rtype: str
        """

        if self.destination_tag:
            builds = self.session.listTagged(self.destination_tag, None, True, latest=2, package=self.component)
        else:
            builds = self.session.listTagged(self.target, None, True, latest=2, package=self.component)

        if self.scratch:
            return builds[0]["nvr"] if builds else None

        return builds[1]["nvr"] if builds and len(builds) > 1 else None

    @cached_property
    def branch(self):
        # pylint: disable=no-self-use

        return None

    @cached_property
    def task_artifacts(self):
        """
        Artifacts of ``buildArch`` subtasks, in a mapping where subtask IDs are the keys
        and lists of artifact names are the values.

        Usually, this is a mix of logs and RPMs, and gets empty when task's directory
        on the server is removed.

        :rtype: dict(int, list(str))
        """

        artifacts = {}

        for task in self._build_arch_subtasks:
            task_id = task['id']

            task_output = self.session.listTaskOutput(task_id)

            log_dict(self.debug, 'task output of subtask {}'.format(task_id), task_output)

            artifacts[task_id] = task_output

        log_dict(self.debug, 'subtask artifacts', artifacts)

        return artifacts

    @cached_property
    def build_artifacts(self):
        """
        Artifacts of the build, in a mapping where architectures are the keys
        and lists of artifact names are the values.

        Usualy, the set consists of RPMs only, and makes sense for builds only, since it is
        not possible to get task RPMs this way.

        :rtype: dict(str, list(str))
        """

        if not self.has_build:
            return {}

        build_rpms = self.session.listBuildRPMs(self.build_id)

        log_dict(self.debug, 'build RPMs', build_rpms)

        artifacts = collections.defaultdict(list)

        for rpm in build_rpms:
            artifacts[rpm['arch']].append(rpm)

        log_dict(self.debug, 'build rpms', artifacts)

        return artifacts

    @cached_property
    def build_archives(self):
        """
        A list of archives of the build.

        :rtype: list(dict)
        """

        if not self.has_build:
            return []

        archives = self.session.listArchives(buildID=self.build_id)
        log_dict(self.debug, 'build archives', archives)

        return archives

    @cached_property
    def has_artifacts(self):
        """
        Whether there are any artifacts on for the task.

        :rtype: bool
        """

        has_task_artifacts = [bool(subtask_artifacts) for subtask_artifacts in self.task_artifacts.itervalues()]
        has_build_artifacts = [bool(arch_artifacts) for arch_artifacts in self.build_artifacts.itervalues()]

        return (has_task_artifacts and all(has_task_artifacts)) \
            or (has_build_artifacts and all(has_build_artifacts))

    @cached_property
    def _srcrpm_subtask(self):
        """
        Search for SRPM-like artifact in ``buildArch`` subtasks, and if there is such artifact,
        provide its name and ID of its subtask. If no such artifact exists, both values are ``None``.

        :rtype: tuple(int, str)
        """

        if not self.has_artifacts:
            self.debug('task has no artifacts, it is pointless to search them for srpm')
            return None, None

        for subtask, artifacts in self.task_artifacts.iteritems():
            for artifact in artifacts:
                if not artifact.endswith('.src.rpm'):
                    continue

                return subtask, artifact

        return None, None

    @cached_property
    def srcrpm(self):
        """
        Source RPM name or ``None`` if it's impossible to find it.

        :rtype: str
        """

        if self._task_info['state'] != koji.TASK_STATES["CLOSED"]:
            raise GlueError('Task {} is not a successfully completed task'.format(self.id))

        # "build container" tasks have no SRPM
        if not self.is_build_task:
            return None

        # For standard (non-scratch) builds, we may fetch an associated build and dig info from it
        if self.has_build:
            self.debug('srpm name deduced from build')
            return '{}.src.rpm'.format(self._build['nvr'])

        # Search all known artifacts for SRPM-like files
        _, srcrpm = self._srcrpm_subtask

        if srcrpm is not None:
            self.debug('srpm name deduced from a subtask artifact')
            return srcrpm

        # Maybe it's in Source option!
        source = self._task_request.options.get('Source', None)
        if source:
            self.debug('srpm name deduced from task Source option')
            return source.split('/')[-1].strip()

        # Or in one of the subtasks!
        for subtask in self._build_arch_subtasks:
            if not subtask['request'].source:
                continue

            self.debug('srpm name deduced from subtask Source option')
            return subtask['request'].source.split('/')[-1].strip()

        # Nope, no SRPM anywhere.
        return None

    @cached_property
    def srcrpm_url(self):
        """
        URL of the SRPM (:py:attr:`srcrpm`) or ``None`` if SRPM is not known.
        """

        if not self.srcrpm:
            return None

        if not self.scratch:
            return "{}/packages/{}/{}/{}/src/{}.src.rpm".format(
                self.pkgs_url,
                self._build['package_name'],
                self._build['version'],
                self._build['release'],
                self._build['nvr']
            )

        srcrpm_task, srcrpm = self._srcrpm_subtask

        # we have SRPM name but no parent task, i.e. it's not possible to construct URL
        if srcrpm_task is None:
            return None

        base_path = koji.pathinfo.taskrelpath(srcrpm_task)

        return '/'.join(['{0}/work'.format(self.pkgs_url), base_path, srcrpm])

    @cached_property
    def _split_srcrpm(self):
        """
        SRPM name split into its NVREA pieces.

        :raises gluetool.glue.GlueError: when SRPM name is not known.
        :rtype: tuple(str)
        """

        if not self.srcrpm:
            raise GlueError('Cannot find SRPM name')

        return splitFilename(self.srcrpm)

    @cached_property
    def nvr(self):
        """
        NVR of the built package.

        :rtype: str
        """

        if self.is_build_task:
            name, version, release, _, _ = self._split_srcrpm

            return '-'.join([name, version, release])

        raise GlueError('Cannot deduce NVR for task {}'.format(self.id))

    @cached_property
    def component(self):
        """
        Package name of the built package (``N`` of ``NVR``).

        :rtype: str
        """

        if self.is_build_task:
            return self._split_srcrpm[0]

        raise GlueError('Cannot find component info for task {}'.format(self.id))

    @cached_property
    def version(self):
        """
        Version of the built package (``V`` of ``NVR``).

        :rtype: str
        """

        if self.is_build_task:
            return self._split_srcrpm[1]

        raise GlueError('Cannot find version info for task {}'.format(self.id))

    @cached_property
    def release(self):
        """
        Release of the built package (``R`` of ``NVR``).

        :rtype: str
        """

        if self.is_build_task:
            return self._split_srcrpm[2]

        raise GlueError('Cannot find release info for task {}'.format(self.id))

    @cached_property
    def full_name(self):
        """
        String with human readable task details. Used for slightly verbose representation e.g. in logs.

        :rtype: str
        """

        name = [
            "task '{}'".format(self.id),
            "build '{}'".format(self.nvr),
            "target '{}'".format(self.target)
        ]

        if self.scratch:
            name.append('(scratch)')

        if not self.has_artifacts:
            name.append('(no artifacts)')

        return ' '.join(name)

    @cached_property
    def short_name(self):
        """
        Short version of :py:attr:`full_name``.

        :rtype: str
        """

        return "{t.id}:{scratch}{t.nvr}".format(t=self, scratch='S:' if self.scratch else '')

    @cached_property
    def destination_tag(self):
        """
        Build destination tag
        """

        try:
            return self.session.getBuildTarget(self.target)["dest_tag_name"]
        except TypeError:
            return None

    @cached_property
    def component_id(self):
        """
        Used by task dispatcher to search their configurations. Identifies the component the task belongs to.

        :rtype: str
        """

        return self.component


class BrewTask(KojiTask):
    """
    Provides abstraction of a brew build task, specified by task ID. For initialization
    brew instance details need to be passed via the instance dictionary with the following keys:

        ``automation_user_ids`` - list of user IDs that trigger resolving of user from dist git
        ``dist_git_commit_urls`` - list of dist git commit urls used to resolve user from dist git
        ``session`` - a koji session initialized via the koji.ClientSession function
        ``url`` - a base URL for the koji instance
        ``pkgs_url`` - a base URL for the packages location

`   This class extends :py:class:`KojiTask` with Brew only features.

    :param dict instance: Instance details, see ``required_instance_keys``
    :param int task_id: Initialize from given TaskID
    :param module: Module that created this task instance.
    :param gluetool.log.ContextLogger logger: logger used for logging
    :param bool wait_timeout: Wait for task to become non-waiting
    """

    ARTIFACT_NAMESPACE = 'brew-build'

    def _check_required_instance_keys(self, details):
        """
        Checks for required instance details for Brew.
        :raises: GlueError if instance is missing some of the required keys
        """
        required_instance_keys = ('automation_user_ids', 'dist_git_commit_urls', 'session', 'url', 'pkgs_url')

        if not all(key in details for key in required_instance_keys):
            raise GlueError('instance details do not contain all required keys')

    # pylint: disable=too-many-arguments
    def __init__(self, details, task_id, module, logger=None, wait_timeout=None):
        super(BrewTask, self).__init__(details, task_id, module, logger, wait_timeout)

        self.automation_user_ids = details['automation_user_ids']
        self.dist_git_commit_urls = details['dist_git_commit_urls']

        if self.is_build_container_task:
            if not self._result or 'koji_builds' not in self._result or not self._result['koji_builds']:
                raise GlueError('Container task {} does not have a result'.format(self.id))

            self._build = self.session.getBuild(int(self._result['koji_builds'][0]))
            log_dict(self.debug, 'build for task ID {}'.format(self.id), self._build)

    @cached_property
    def is_build_container_task(self):
        return self._task_info['method'] == 'buildContainer'

    @cached_property
    def has_artifacts(self):
        """
        Whether there are any artifacts on for the task.

        :rtype: bool
        """

        if self.is_build_container_task:
            return bool(self.build_archives)

        return super(BrewTask, self).has_artifacts

    @cached_property
    def source_members(self):
        """
        Return :py:attr:`source` attribute split into its pieces, a component and a GIT commit hash.

        :rtype: tuple(str, str)
        """

        try:
            git_hash = re.search("#[^']*", self.source).group()[1:]
            component = re.search("/rpms/[^#?]*", self.source).group()[6:]

            return component, git_hash

        # pylint: disable=bare-except
        except:
            return None, None

    @cached_property
    def _parsed_commit_html(self):
        """
        :returns: BeatifulSoup4 parsed html from cgit for given component and commit hash
        """

        component, git_hash = self.source_members

        if not component or not git_hash:
            return None

        # get git commit html
        for url in self.dist_git_commit_urls:
            url = url.format(component=component, commit=git_hash)

            try:
                _, content = fetch_url(url, logger=self.logger)
                return BeautifulSoup(content, 'html.parser')

            except GlueError:
                self.warn("Failed to fetch commit info from '{}'".format(url))

        return None

    @cached_property
    def nvr(self):
        """
        NVR of the built package.

        :rtype: str
        """

        if self.is_build_container_task:
            if self.has_build:
                return self._build['nvr']

            return '-'.join([self.component, self.version, self.release])

        return super(BrewTask, self).nvr

    @cached_property
    def component(self):
        """
        Package name of the built package (``N`` of ``NVR``).

        :rtype: str
        """

        if self.is_build_container_task:
            if self.has_build:
                return self._build['package_name']

            component, _ = self.source_members

            if component:
                return component

        return super(BrewTask, self).component

    @cached_property
    def version(self):
        """
        Version of the built package (``V`` of ``NVR``).

        :rtype: str
        """

        if self.is_build_container_task:
            if self.has_build:
                return self._build['version']

            # there is no such field in task info, just in build info :/

        return super(BrewTask, self).version

    @cached_property
    def release(self):
        """
        Release of the built package (``R`` of ``NVR``).

        :rtype: str
        """

        if self.is_build_container_task:
            if self.has_build:
                return self._build['release']

            release = self._task_request.options.get('release', None)

            if release:
                return release

        return super(BrewTask, self).release

    @cached_property
    def branch(self):
        """
        :returns: git branches of brew task or None if branch could not be found
        """
        if self._parsed_commit_html is None:
            return None

        try:
            branches = [branch.string for branch in self._parsed_commit_html.find_all(class_='branch-deco')]
            return ' '.join(branches)
        except AttributeError:
            raise GlueError("could not find 'branch-deco' class in html output of cgit, please inspect")

    @cached_property
    def issuer(self):
        """
        :returns: issuer of brew task and in case of build from automation, returns issuer of git commit
        """
        owner_id = self._task_info["owner"]
        if owner_id not in self.automation_user_ids:
            return self.owner

        self.info("Automation user detected, need to get git commit issuer")
        if self._parsed_commit_html is None:
            self.warn('could not find git commit issuer', sentry=True)
            return self.owner

        issuer = self._parsed_commit_html.find(class_='commit-info').find('td')
        issuer = re.sub(".*lt;(.*)@.*", "\\1", str(issuer))

        return issuer

    @cached_property
    def rhel(self):
        """
        :returns: major version of RHEL
        """
        return re.sub(".*rhel-(\\d+).*", "\\1", self.target)


class Koji(gluetool.Module):
    """
    Provide various information related to a task from Koji build system.

    The task can be specified using on the command line with
        - option ``--build-id`` with a build ID
        - options ``--name`` and ``--tag`` with the latest build from the given tag
        - option ``--nvr`` with a string with an NVR of a build
        - option ``--task-id`` with a build task ID

    The task can be specified also by using the ``task`` shared function. The shared function
    supports only initialization from task ID.
    """

    name = 'koji'
    description = 'Provide Koji task details to other modules'

    options = {
        'url': {
            'help': 'Koji Hub instance base URL',
        },
        'pkgs-url': {
            'help': 'Koji packages base URL',
        },
        'web-url': {
            'help': 'Koji instance web ui URL',
        },
        'task-id': {
            'help': 'Initialize from task ID.',
            'action': 'append',
            'default': [],
            'type': int
        },
        'build-id': {
            'help': 'Initialize from build ID.',
            'action': 'append',
            'default': [],
            'type': int
        },
        'name': {
            'help': 'Initialize from package name, by choosing latest tagged build (requires ``--tag``).',
            'action': 'append',
            'default': []
        },
        'nvr': {
            'help': 'Initialize from package NVR.',
            'action': 'append',
            'default': []
        },
        'tag': {
            'help': 'Use given build tag.',
        },
        'valid-methods': {
            'help': """
                    List of task methods that are considered valid, e.g. ``build`` or ``buildContainer``
                    (Default: any method is considered valid).
                    """,
            'metavar': 'METHOD1,METHOD2,...',
            'action': 'append',
            'default': []
        },
        'wait': {
            'help': 'Wait timeout for task to become non-waiting and closed',
            'type': int,
            'default': 60,
        }
    }

    options_note = """
    Options ``--task-id``, ``--build-id``, ``--name`` and ``--nvr`` can be used multiple times, and even mixed
    together, to specify tasks for a single pipeline in many different ways.
    """

    required_options = ['url', 'pkgs-url', 'web-url']
    shared_functions = ('tasks', 'primary_task', 'koji_session')

    def __init__(self, *args, **kwargs):
        super(Koji, self).__init__(*args, **kwargs)

        self._session = None
        self._tasks = []

    @cached_property
    def _valid_methods(self):
        return gluetool.utils.normalize_multistring_option(self.option('valid-methods'))

    def _task_factory(self, task_id, wait_timeout=None, details=None, task_class=None):
        task_class = task_class or KojiTask

        details = dict_update({
            'session': self._session,
            'url': self.option('url'),
            'pkgs_url': self.option('pkgs-url'),
            'web_url': self.option('web-url'),
        }, details or {})

        task = task_class(details, task_id, self, logger=self.logger, wait_timeout=wait_timeout)

        return task

    def _objects_to_builds(self, name, object_ids, finder):
        if not object_ids:
            return []

        log_dict(self.debug, 'finding builds for {} ids'.format(name), object_ids)

        builds = []

        for object_id in object_ids:
            build = finder(object_id)

            log_dict(self.debug, "for '{}' found".format(object_id), build)

            if None in build:
                self.warn('Looking for {} {}, remote server returned None - skipping this ID'.format(name, object_id))
                continue

            builds += build

        log_dict(self.debug, 'found builds', builds)

        return builds

    def _find_task_ids(self, task_ids=None, build_ids=None, nvrs=None, names=None):
        """
        Tries to gather all available task IDs for different given inputs - build IDs, NVRs, package names
        and actual task IDs as well. Some of these may be unknown to the backend, some of them may not lead
        to a task ID. This helper method will find as many task IDs as possible.

        :param list(int) task_ids: Task IDs
        :param list(int) build_ids: Build IDs.
        :param list(str) nvr: Package NVRs.
        :param list(str) names: Package names. The latest build with a tag - given via module's ``--tag``
            option - is the possible solution.
        :rtype: list(int)
        :return: Gathered task IDs.
        """

        log_dict(self.debug, 'find task IDs - from task IDs', task_ids)
        log_dict(self.debug, 'find task IDs - from build IDs', build_ids)
        log_dict(self.debug, 'find task IDs - from NVRs', nvrs)
        log_dict(self.debug, 'find task IDs - from names', names)

        # Task IDs are easy - just use them as an initial value of the list we want to return.
        task_ids = task_ids or []

        # Other options represent builds, and from those builds we must extract their tasks. First, let's find
        # all those builds.
        builds = []

        builds += self._objects_to_builds('build', build_ids,
                                          lambda build_id: [self._session.getBuild(build_id)])
        builds += self._objects_to_builds('nvr', nvrs,
                                          lambda nvr: [self._session.getBuild(nvr)])
        builds += self._objects_to_builds('name', names,
                                          lambda name: self._session.listTagged(self.option('tag'), package=name))

        # Now extract task IDs.
        for build in builds:
            if 'task_id' not in build or not build['task_id']:
                log_dict(self.debug, 'Build does not provide build ID', build)
                continue

            task_ids.append(build['task_id'])

        return [int(task_id) for task_id in task_ids]

    def koji_session(self):
        return self._session

    def _assert_tasks(self):
        if not self._tasks:
            raise GlueError('No tasks specified.')

    def tasks(self, task_ids=None, build_ids=None, nvrs=None, names=None, **kwargs):
        """
        Returns a list of current tasks. If options are specified, new set of tasks is created using
        the provided options to find all available tasks, and this set becomes new set of current tasks,
        which is then returned.

        Method either returns non-empty list of tasks, or raises an exception

        :param list(int) task_ids: Task IDs
        :param list(int) build_ids: Build IDs.
        :param list(str) nvr: Package NVRs.
        :param list(str) names: Package names. The latest build with a tag - given via module's ``--tag``
            option - is the possible solution.
        :param dict kwargs: Additional arguments passed to :py:meth:`_task_factory`.
        :rtype: list(KojiTask)
        :returns: Current task instances.
        :raises gluetool.glue.GlueError: When there are no tasks.
        """

        # Re-initialize set of current tasks only when any of the options is set.
        # Otherwise leave it untouched.
        if any([task_ids, build_ids, nvrs, names]):
            self._tasks = [
                self._task_factory(task_id, **kwargs)
                for task_id in self._find_task_ids(task_ids=task_ids, build_ids=build_ids, nvrs=nvrs, names=names)
            ]

        self._assert_tasks()

        return self._tasks

    def primary_task(self):
        """
        Returns a `primary` task, the first task in the list of current tasks.

        Method either returns a task, or raises an exception.

        :rtype: :py:class:`KojiTask`
        :raises gluetool.glue.GlueError: When there are no tasks, therefore not even a primary one.
        """

        log_dict(self.debug, 'primary task - current tasks', self._tasks)

        self._assert_tasks()

        return self._tasks[0]

    @property
    def eval_context(self):
        # pylint: disable=unused-variable
        __content__ = {  # noqa
            # common for all artifact providers
            'ARTIFACT_TYPE': """
                             Type of the artifact, either ``koji-build`` or ``brew-build``.
                             """,
            'BUILD_TARGET': """
                            Build target of the primary task, as known to Koji/Brew.
                            """,
            'NVR': """
                   NVR of the primary task.
                   """,
            'PRIMARY_TASK': """
                            Primary task, represented as ``KojiTask`` or ``BrewTask`` instance.
                            """,
            'TASKS': """
                     List of all tasks known to this module instance.
                     """,

            # Brew/Koji specific
            'SCRATCH': """
                       ``True`` if the primary task represents a scratch build, ``False`` otherwise.
                       """
        }

        primary_task = self.primary_task()

        return {
            # common for all artifact providers
            'ARTIFACT_TYPE': primary_task.ARTIFACT_NAMESPACE,
            'BUILD_TARGET': primary_task.target,
            'NVR': primary_task.nvr,
            'PRIMARY_TASK': primary_task,
            'TASKS': self.tasks(),

            # Brew/Koji specific
            'SCRATCH': primary_task.scratch
        }

    def sanity(self):
        # make sure that no conflicting options are specified

        # name option requires tag
        if self.option('name') and not self.option('tag'):
            raise GlueError("You need to specify 'tag' with package name")

        # name option requires tag
        if self.option('tag') and not self.option('name'):
            raise GlueError("You need to specify package name with '--name' option")

    def execute(self):
        url = self.option('url')
        wait_timeout = self.option('wait')

        self._session = koji.ClientSession(url)
        version = self._session.getAPIVersion()
        self.info('connected to {} instance \'{}\' API version {}'.format(self.unique_name, url, version))

        task_ids = self._find_task_ids(task_ids=self.option('task-id'),
                                       build_ids=self.option('build-id'),
                                       nvrs=normalize_multistring_option(self.option('nvr')),
                                       names=normalize_multistring_option(self.option('name')))

        if task_ids:
            self.tasks(task_ids=task_ids, wait_timeout=wait_timeout)

        for task in self._tasks:
            self.info('initialized task {}'.format(task.full_name))

        for task in self._tasks:
            if not task.has_artifacts:
                raise NoArtifactsError(task.id)


class Brew(Koji, (gluetool.Module)):
    """
    Provide various information related to a task from Brew build system.

    The task can be specified using on the command line with
        - option ``--build-id`` with a build ID
        - options ``--name`` and ``--tag`` with the latest build from the given tag
        - option ``--nvr`` with a string with an NVR of a build
        - option ``--task-id`` with a build task ID
    """
    name = 'brew'
    description = 'Provide Brew task details to other modules'

    options = dict_update({}, Koji.options, {
        'automation-user-ids': {
            'help': 'List of comma delimited user IDs that trigger resolving of issuer from dist git commit instead'
        },
        'dist-git-commit-urls': {
            'help': 'List of comma delimited dist git commit urls used for resolving of issuer from commit'
        }
    })

    required_options = Koji.required_options + ['automation-user-ids', 'dist-git-commit-urls']

    def _task_factory(self, task_id, wait_timeout=None, details=None, task_class=None):
        # options checker does not handle multiple modules in the same file correctly, therefore it
        # raises "false" negative for the following use of parent's class options
        # pylint: disable=gluetool-unknown-option
        details = dict_update({}, {
            'automation_user_ids': [int(user.strip()) for user in self.option('automation-user-ids').split(',')],
            'dist_git_commit_urls': [url.strip() for url in self.option('dist-git-commit-urls').split(',')]
        }, details or {})

        return super(Brew, self)._task_factory(task_id, details=details, task_class=BrewTask,
                                               wait_timeout=wait_timeout)
