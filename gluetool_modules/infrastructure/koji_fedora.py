import collections
import re
import koji
from bs4 import BeautifulSoup

import gluetool
from gluetool import GlueError, SoftGlueError
from gluetool.log import Logging, log_dict
from gluetool.utils import cached_property, dict_update, fetch_url, format_dict, wait


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


class KojiTask(object):
    """
    Provides abstraction of a koji build task, specified by task ID. For initialization
    koji instance details need to be passed via the instance dictionary with the following keys:

        ``session`` - a koji session initialized via the koji.ClientSession function
        ``url`` - a base URL for the koji instance
        ``pkgs_url`` - a base URL for the packages location

    :param dict details: Instance details, see ``required_instance_keys``
    :param int task_id: Initialize from given TaskID
    :param str module_name: Name of the module, i.e. 'brew' or 'koji'
    :param gluetool.log.ContextLogger logger: logger used for logging
    :param bool wait_timeout: Wait for task to become non-waiting
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
    def __init__(self, details, task_id, module_name, logger=None, wait_timeout=None):
        self._check_required_instance_keys(details)

        if not isinstance(details['session'], koji.ClientSession):
            raise GlueError('session is not a koji client session instance')

        self.logger = logger or Logging.get_logger()
        logger.connect(self)

        self.task_id = int(task_id)
        self.api_url = details['url']
        self.web_url = details['web_url']
        self.pkgs_url = details['pkgs_url']
        self.session = details['session']
        self.module_name = module_name
        self.artifact_id = self.task_id

        # first check if the task is valid for our case
        if not self._valid_task():
            raise NotBuildTaskError(self.task_id)

        # this string identifies component in static config file
        self.component_id = self.component

        # wait for the task to be non-waiting and closed
        wait('waiting for task to be non waiting', self._check_nonwaiting_task, timeout=wait_timeout)

        # wait for task to be in CLOSED state
        # note that this can take some amount of time after it becomes non-waiting
        wait('waiting for task to be closed', self._check_closed_task, timeout=wait_timeout)

    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, self.task_id)

    def _valid_task(self):
        """
        Verify that the task is valid for our case, i.e. method is build.

        :returns: True if task is a sucessfully finished build task, False otherwise
        """
        if self.task_info['method'] == 'build':
            return True

        return False

    def _check_closed_task(self):
        """
        Verify that the task is closed.

        :returns: True if task is closed, False otherwise
        """
        # remove the cached task_info
        del self.task_info

        if self.task_info['state'] == koji.TASK_STATES['CLOSED']:
            return True

        return False

    def _check_nonwaiting_task(self):
        """
        Check if task is non-waiting, i.e. 'waiting: false' in task info.
        :returns: True if task is non-waiting, False otherwise
        """
        # remove the cached task_info
        del self.task_info

        if self.task_info['waiting'] is False:
            return True

        return False

    @cached_property
    def _children(self):
        """
        Return list of children task in raw form, as JSON data returned by Koji API.

        :rtype: list(dict)
        """

        children = self.session.getTaskChildren(self.task_id, request=False)
        log_dict(self.debug, 'child tasks', children)

        return children

    @cached_property
    def build(self):
        """
        :returns: returns build info dictionary for a given standard task or None for scratch
        """
        if self.scratch:
            return None
        return self.session.listBuilds(taskID=self.task_id)[0]

    @cached_property
    def build_id(self):
        """
        :returns: build id for standard tasks or None for a scratch task
        """
        if self.scratch:
            return None
        return self.build['build_id']

    @cached_property
    def task_info(self):
        """
        :returns: dictionary with task details
        """
        task_info = self.session.getTaskInfo(self.task_id, request=True)
        if not task_info:
            raise GlueError("brew task '{}' not found".format(self.task_id))

        self.debug('task info:\n{}'.format(format_dict(task_info)))

        return task_info

    @cached_property
    def owner(self):
        """
        :returns: owner name of task
        """
        owner_id = self.task_info["owner"]
        return self.session.getUser(owner_id)["name"]

    @cached_property
    def issuer(self):
        """
        :returns: issuer of a task (same as owner for Koji)
        """
        return self.owner

    @cached_property
    def _task_info_request(self):
        if 'request' not in self.task_info:
            raise GlueError("task '{}' has no request field in task info".format(self.task_id))

        if len(self.task_info["request"]) < 3:
            raise GlueError("task '{}' has unexpected number of items in request field".format(self.task_id))

        return self.task_info["request"]

    @cached_property
    def target(self):
        """
        :returns: build target name
        """
        if self._task_info_request[1]:
            return self._task_info_request[1]

        # inform admins about this weird build
        self.warn("task '{}' build '{}' has no build target".format(self.task_id, self.nvr), sentry=True)

        return '<no build target available>'

    @cached_property
    def source(self):
        """
        :returns: task's source, e.g. git+https://src.fedoraproject.org/rpms/rust-tokio-proto.git?#b59219
        """
        if self._task_info_request[0]:
            return self._task_info_request[0]

        raise GlueError("task '{}' has no source defined in the request field".format(self.task_id))

    @cached_property
    def scratch(self):
        """
        :returns: True if task is scratch, False if not
        """
        if "scratch" in self._task_info_request[2]:
            return self._task_info_request[2]["scratch"]
        return False

    @cached_property
    def task_arches(self):
        """
        Return information about arches the task was building for.

        :rtype: TaskArches
        """

        arches = self._task_info_request[2].get('arch_override', None)

        if arches is not None:
            return TaskArches(False, [arch.strip() for arch in arches.split(' ')])

        return TaskArches(True, [child['arch'] for child in self._children if child['method'] == 'buildArch'])

    @cached_property
    def url(self):
        """
        :returns: URL of task info web page
        """
        return "{}/taskinfo?taskID={}".format(self.web_url, self.task_id)

    @cached_property
    def latest(self):
        """
        :returns: latest released package with the same destination tag or None if not found
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
    def srcrpm(self):
        """
        :returns: URL to the source rpm or None if artifacts gone (for scratch build only)
        """
        if self.task_info['state'] != koji.TASK_STATES["CLOSED"]:
            raise GlueError("Brew task [%s] is not a successfully completed task" % self.task_id)

        # For standard (non-scratch) builds, we may fetch an associated build and dig info from it
        if not self.scratch:
            return "{}/packages/{}/{}/{}/src/{}.src.rpm".format(
                self.pkgs_url,
                self.build['package_name'],
                self.build['version'],
                self.build['release'],
                self.build['nvr']
            )

        # For scratch build there is no associated build, so we need to go through all buildArch tasks
        tasks = self.session.listTasks(opts={
            'parent': self.task_id,
            'method': 'buildArch',
            'state': [koji.TASK_STATES['CLOSED']],
            'decode': True
        })

        # gather list of files for each (sub-)task. We'll end up with this list:
        # [(task1, file1), (task1, file2), ..., (taskN, fileM)]
        tasks_outputs = []
        for task in tasks:
            tasks_outputs += [(task, filename) for filename in self.session.listTaskOutput(task['id'])]

        # find src.rpm in the outputs
        for task, filename in tasks_outputs:
            if not filename.endswith('.src.rpm'):
                continue

            base_path = koji.pathinfo.taskrelpath(task['id'])
            return '/'.join(['{0}/work'.format(self.pkgs_url), base_path, filename])

        raise NoArtifactsError(self.task_id)

    @cached_property
    def nvr(self):
        """
        :returns: NVR of the build task
        """
        return ".".join(self.srcrpm.split("/")[-1].split(".")[:-2])

    @cached_property
    def component(self):
        """
        :returns: package name of the build task
        """
        return "-".join(self.nvr.split("-")[:-2])

    @cached_property
    def version(self):
        """
        :returns: version of the build task
        """
        return self.nvr.split("-")[-2]

    @cached_property
    def release(self):
        """
        :returns: release of the build task
        """
        return self.nvr.split("-")[-1]

    @cached_property
    def full_name(self):
        """
        :returns: string with human readable task details
        """
        msg = ["task '{}'".format(self.task_id)]
        if self.scratch:
            msg.append("scratch")
        msg.append("build '{}'".format(self.nvr))
        msg.append("target '{}'".format(self.target))
        return ' '.join(msg)

    @cached_property
    def short_name(self):
        """
        :returns: short version string with task details
        """
        return "{t.task_id}:{scratch}{t.nvr}".format(t=self, scratch='S:' if self.scratch else '')

    @cached_property
    def destination_tag(self):
        """
        :returns: build destination tag
        """
        try:
            return self.session.getBuildTarget(self.target)["dest_tag_name"]
        except TypeError:
            return None


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
    :param str module_name: Name of the module, i.e. 'brew' or 'koji'
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
    def __init__(self, details, task_id, module_name, logger=None, wait_timeout=None):
        super(BrewTask, self).__init__(details, task_id, module_name, logger, wait_timeout)
        self.automation_user_ids = details['automation_user_ids']
        self.dist_git_commit_urls = details['dist_git_commit_urls']

    @cached_property
    def _parsed_commit_html(self):
        """
        :returns: BeatifulSoup4 parsed html from cgit for given component and commit hash
        """
        # get git commit hash and component name
        try:
            git_hash = re.search("#[^']*", self.source).group()[1:]
            component = re.search("/rpms/[^?]*", self.source).group()[6:]
        except AttributeError:
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
        owner_id = self.task_info["owner"]
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
        'wait': {
            'help': 'Wait timeout for task to become non-waiting and closed',
            'type': int,
            'default': 60,
        },
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

    def _task_factory(self, task_id, wait_timeout=None, details=None, task_class=None):
        task_class = task_class or KojiTask

        details = dict_update({
            'session': self._session,
            'url': self.option('url'),
            'pkgs_url': self.option('pkgs-url'),
            'web_url': self.option('web-url'),
        }, details or {})

        task = task_class(details, task_id, self.unique_name, logger=self.logger, wait_timeout=wait_timeout)
        self.debug('initialized {}'.format(task.full_name))

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

    def _find_task_ids(self):
        """
        Tries to find task ID from all supported options.

        :return: task ID
        :rtype: int or None if no sources specified
        """

        task_ids = []
        builds = []

        if self.option('task-id'):
            task_ids += self.option('task-id')

        if self.option('build-id'):
            builds += self._objects_to_builds('build', self.option('build-id'),
                                              lambda build_id: [self._session.getBuild(build_id)])

        if self.option('nvr'):
            builds += self._objects_to_builds('nvr', self.option('nvr'),
                                              lambda nvr: [self._session.getBuild(nvr)])

        if self.option('name'):
            builds += self._objects_to_builds('name', self.option('name'),
                                              lambda name: self._session.listTagged(self.option('tag'), package=name))

        if builds:
            for_removal = []

            for build in builds:
                if 'task_id' in build and build['task_id']:
                    continue

                log_dict(self.debug, 'Build does not provide build ID', build)
                for_removal.append(build)

            for build in for_removal:
                builds.remove(build)

            task_ids += [build['task_id'] for build in builds]

        return task_ids

    def koji_session(self):
        return self._session

    def _assert_tasks(self):
        if not self._tasks:
            raise GlueError('No tasks specified.')

    def tasks(self, task_ids=None, **kwargs):
        """
        Returns a list of current tasks. If ``task_ids`` is set, new set of tasks is created using
        the IDs, and becomes new set of current tasks, which is then returned.

        Method either returns non-empty list of tasks, or raises an exception

        :param list(int) task_ids: IDs of the tasks.
        :param dict kwargs: Additional arguments passed to :py:meth:`_task_factory`.
        :returns: List of task instances.
        :rtype: list(:py:class:`KojiTask`)
        :raises gluetool.glue.GlueError: When there are no tasks.
        """

        task_ids = task_ids or []

        log_dict(self.debug, 'tasks - for IDs', task_ids)

        if task_ids:
            self._tasks = [self._task_factory(task_id, **kwargs) for task_id in task_ids]

        self._assert_tasks()

        return self._tasks

    def primary_task(self):
        """
        Returns a `primary` task, the first task in the list of current tasks.

        Method either returns a task, or raises an exception.

        :rtype: :py:class:`KojiTask`
        :raises gluetool.glue.GlueError: When there are no tasks, therefore not even a primary one.
        """

        self.debug('primary task - current tasks: {}'.format(format_dict(self._tasks)))

        self._assert_tasks()

        return self._tasks[0]

    @property
    def eval_context(self):
        """
        Provides informations about Koji/Brew artifacts.

        Provides following variables: BUILD_TARGET, PRIMARY_TASK, TASKS, NVR, SCRATCH

        :rtype: dict
        """

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

        task_ids = self._find_task_ids()
        if task_ids:
            self.tasks(task_ids, wait_timeout=wait_timeout)

        for task in self._tasks:
            self.info('initialized {}'.format(task.full_name))


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
