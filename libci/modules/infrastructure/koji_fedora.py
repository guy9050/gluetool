import koji
from libci import CIError, SoftCIError, Module
from libci.log import Logging
from libci.utils import cached_property, format_dict, wait


class NotBuildTaskError(SoftCIError):
    SUBJECT = 'Task id does not point to a valid build task'
    BODY = """
Task id passed to the koji module does not point to a valid build task. CI needs a valid build task to work. If you entered the task id manually you might have passed in an incorrect id. If this failure comes from automated build, something is obviously wrong and this incident should be reported as a bug.
    """

    def __init__(self, task_id):
        super(NotBuildTaskError, self).__init__("task '{}' is not a valid finished build task".format(task_id))


class NoArtifactsError(SoftCIError):
    SUBJECT = 'No artifacts found for the koji task'
    BODY = """
Koji task has no artifacts - packages, logs, etc. This can happen e.g. in the case of scratch
builds - their artifacts are removed from koji few days after their completion.
    """

    def __init__(self, task_id):
        msg = "no artifacts found for koji task '{}', expired scratch build?".format(task_id)
        super(NoArtifactsError, self).__init__(msg)


class KojiTask(object):
    """
    Provides abstraction of a koji build task, specified by task ID. For initialization
    koji instance details need to be passed via the instance dictionary with the following keys:

        ``session`` - a koji session initialized via the koji.ClientSession function
        ``url`` - a base URL for the koji instance
        ``pkgs_url`` - a base URL for the packages location

    :param dict instance: Instance details, see ``required_instance_keys``
    :param int task_id: Initialize from given TaskID
    :param libci.log.ContextLogger logger: logger used for logging
    :param bool wait_timeout: Wait for task to become non-waiting
    """

    def __init__(self, details, task_id, logger=None, wait_timeout=None):
        required_instance_keys = ('session', 'url', 'pkgs_url')

        if not all(key in details for key in required_instance_keys):
            raise CIError('instance details do not contain all required keys')

        if not isinstance(details['session'], koji.ClientSession):
            raise CIError('session is not a koji client session instance')

        self.logger = logger or Logging.get_logger()
        logger.connect(self)

        self.task_id = int(task_id)
        self.api_url = details['url']
        self.pkgs_url = details['pkgs_url']
        self.session = details['session']

        if wait_timeout:
            wait('waiting for task to be non waiting', self._check_nonwaiting_task, timeout=wait_timeout)

        if not self._valid_task():
            raise NotBuildTaskError(self.task_id)

    def _valid_task(self):
        """
        Verify that the task is a sucessfully finished build task

        :returns: True if task is a sucessfully finished build task, False otherwise
        """
        if self.task_info['state'] != koji.TASK_STATES['CLOSED'] or self.task_info['method'] != 'build':
            return False

        return True

    def _check_nonwaiting_task(self):
        """
        Check if task is non-waiting, i.e. 'waiting: false' in task info.
        :returns: True if task is non-waiting, False otherwise
        """

        # do not use cached task_info here
        task_info = self.session.getTaskInfo(self.task_id, request=True)
        if task_info['waiting'] is False:
            return True

        return False

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
            raise CIError("brew task '{}' not found".format(self.task_id))

        self.debug('task info:\n{}'.format(format_dict(task_info)))

        return task_info

    @cached_property
    def owner(self):
        """
        :returns: owner property of brew task
        """
        owner_id = self.task_info["owner"]
        return self.session.getUser(owner_id)["name"]

    @cached_property
    def issuer(self):
        """
        :returns: issuer (owner) of brew task
        """
        return self.owner

    @cached_property
    def target(self):
        """
        :returns: build target name
        """
        return self.task_info["request"][1]

    @cached_property
    def scratch(self):
        """
        :returns: True if task is scratch, False if not
        """
        if "scratch" in self.task_info["request"][2]:
            return self.task_info["request"][2]["scratch"]
        return False

    @cached_property
    def url(self):
        """
        :returns: URL of task info web page
        """
        return "{}/taskinfo?taskID={}".format(self.api_url, self.task_id)

    @cached_property
    def latest(self):
        """
        :returns: latest released package with the same destination tag or None if not found
        """
        builds = self.session.listTagged(self.destination_tag, None, True, latest=2, package=self.component)
        if self.scratch:
            return builds[0]["nvr"] if builds else None
        return builds[1]["nvr"] if builds and len(builds) > 1 else None

    @cached_property
    def srcrpm(self):
        """
        :returns: URL to the source rpm or None if artifacts gone (for scratch build only)
        """
        if self.task_info['state'] != koji.TASK_STATES["CLOSED"]:
            raise CIError("Brew task [%s] is not a successfully completed task" % self.task_id)

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
        msg.append("destination tag '{}'".format(self.destination_tag))
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
        return self.session.getBuildTarget(self.target)["dest_tag_name"]


class CIKoji(Module):
    """
    Provide various information related to a task from Fedora Koji instance.

    The koji task can be specified using on the command line with
        - option ``--build-id`` with a build ID
        - options ``--name`` and ``--tag`` with the latest build from the given tag
        - option ``--nvr`` with a string with an NVR of a build
        - option ``--task-id`` with a build task ID

    The task can be specified also by using the ``task`` shared function. The shared function
    supports only initialization from task ID.
    """

    name = 'koji'
    description = 'Provide Fedora koji task details to other modules'

    options = {
        'url': {
            'help': 'Koji instance base URL',
        },
        'pkgs-url': {
            'help': 'Koji packages base URL',
        },
        'task-id': {
            'help': 'Initialize from koji task ID',
            'type': int,
        },
        'build-id': {
            'help': 'Initialize from koji build ID',
            'type': int,
        },
        'name': {
            'help': 'Choose latest tagged build of the given package name (requires --tag)',
        },
        'nvr': {
            'help': 'Initialize from given NVR',
        },
        'tag': {
            'help': 'Use give build tag',
        },
        'wait': {
            'help': 'Wait for given number of seconds for a not finished task',
            'type': int,
        }

    }
    required_options = ['url', 'pkgs-url']
    shared_functions = ['task']

    def __init__(self, *args, **kwargs):
        super(CIKoji, self).__init__(*args, **kwargs)

        self.koji_instance = None
        self.koji_task_instance = None

    def _init_koji_task(self, task_id, wait_timeout=None):
        details = {
            'session': self.koji_instance,
            'url': self.option('url'),
            'pkgs_url': self.option('pkgs-url'),
        }
        self.koji_task_instance = KojiTask(details, task_id, logger=self.logger, wait_timeout=wait_timeout)
        self.info(self.koji_task_instance.full_name)

    def _find_task_id(self):
        """
        Tries to find task ID from all supported sources.

        :return: task ID
        :rtype: int or None if no sources specified
        """
        build_id = self.option('build-id')
        name = self.option('name')
        nvr = self.option('nvr')
        tag = self.option('tag')
        task_id = self.option('task-id')

        if task_id:
            return task_id

        if build_id:
            builds = [self.koji_instance.getBuild(build_id)]
        elif nvr:
            builds = [self.koji_instance.getBuild(nvr)]
        elif name:
            builds = self.koji_instance.listTagged(tag, package=name)
        else:
            # no task to find, just continue without initialization
            # it will be expected that user inits task via the shared function
            return None

        if builds[0] and 'task_id' in builds[0] and builds[0]['task_id']:
            return builds[0]['task_id']

        raise CIError('could not find a valid build according to given details')

    def task(self, task_id=None):
        """
        Return a KojiTask instance. If task_id passed, initialize KojiTask instance
        from it first.

        :param int task_id: ID of the task to process
        :returns: :py:class:`KojiTask` instance
        """
        if task_id:
            self._init_koji_task(task_id)

        if self.koji_task_instance:
            return self.koji_task_instance

        raise CIError('no koji task ID specified')

    def sanity(self):
        # make sure that no conflicting options are specified
        optnum = sum([1 for opt in ['task-id', 'build-id', 'name', 'nvr'] if self.option(opt) is not None])
        if optnum > 1:
            raise CIError("Only one of the options 'task-id', 'build-id', 'name' and 'nvr' can be specified.")

        # name option requires tag
        if self.option('name') and not self.option('tag'):
            raise CIError("You need to specify 'tag' with package name")

        # name option requires tag
        if self.option('tag') and not self.option('name'):
            raise CIError("You need to specify package name with '--name' option")

    def execute(self):
        url = self.option('url')
        wait_timeout = self.option('wait')

        self.koji_instance = koji.ClientSession(url)
        version = self.koji_instance.getAPIVersion()
        self.info('connected to koji instance \'{}\' API version {}'.format(url, version))

        task_id = self._find_task_id()
        if task_id:
            self._init_koji_task(task_id, wait_timeout=wait_timeout)
