import re
import koji
from bs4 import BeautifulSoup
from libci import CIError, SoftCIError, Module
from libci.log import Logging
from libci.utils import cached_property, dict_update, fetch_url, format_dict, wait


class NotBuildTaskError(SoftCIError):
    SUBJECT = 'Task id does not point to a valid build task'
    BODY = """
Task id passed to the {} module does not point to a valid build task. CI needs a valid build task to work. If you entered the task id manually you might have passed in an incorrect id. If this failure comes from automated build, something is obviously wrong and this incident should be reported as a bug.
    """

    def __init__(self, task_id, name):
        NotBuildTaskError.BODY = NotBuildTaskError.BODY.format(name)
        NotBuildTaskError.SUBJECT = NotBuildTaskError.SUBJECT.format(name)

        super(NotBuildTaskError, self).__init__("task '{}' is not a valid finished build task".format(task_id))


class NoArtifactsError(SoftCIError):
    SUBJECT = 'No artifacts found for the {} task'
    BODY = """
Koji task has no artifacts - packages, logs, etc. This can happen e.g. in the case of scratch
builds - their artifacts are removed from {} few days after their completion.
    """

    def __init__(self, task_id, name):
        NoArtifactsError.BODY = NoArtifactsError.BODY.format(name)
        NoArtifactsError.SUBJECT = NoArtifactsError.SUBJECT.format(name)

        msg = "no artifacts found for {} task '{}', expired scratch build?".format(name, task_id)
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
    :param str module_name: Name of the module, i.e. 'brew' or 'koji'
    :param libci.log.ContextLogger logger: logger used for logging
    :param bool wait_timeout: Wait for task to become non-waiting
    """

    @staticmethod
    def _check_required_instance_keys(details):
        """
        Checks for required instance details for Koji.
        :raises: CIError if instance is missing some of the required keys
        """
        required_instance_keys = ('session', 'url', 'pkgs_url', 'web_url')

        if not all(key in details for key in required_instance_keys):
            raise CIError('instance details do not contain all required keys')

    # pylint: disable=too-many-arguments
    def __init__(self, details, task_id, module_name, logger=None, wait_timeout=None):
        self._check_required_instance_keys(details)

        if not isinstance(details['session'], koji.ClientSession):
            raise CIError('session is not a koji client session instance')

        self.logger = logger or Logging.get_logger()
        logger.connect(self)

        self.task_id = int(task_id)
        self.api_url = details['url']
        self.web_url = details['web_url']
        self.pkgs_url = details['pkgs_url']
        self.session = details['session']
        self.module_name = module_name

        if wait_timeout:
            wait('waiting for task to be non waiting', self._check_nonwaiting_task, timeout=wait_timeout)

        if not self._valid_task():
            raise NotBuildTaskError(self.task_id, module_name)

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
        return "{}/taskinfo?taskID={}".format(self.web_url, self.task_id)

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

        raise NoArtifactsError(self.task_id, self.module_name)

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
    :param libci.log.ContextLogger logger: logger used for logging
    :param bool wait_timeout: Wait for task to become non-waiting
    """

    def _check_required_instance_keys(self, details):
        """
        Checks for required instance details for Brew.
        :raises: CIError if instance is missing some of the required keys
        """
        required_instance_keys = ('automation_user_ids', 'dist_git_commit_urls', 'session', 'url', 'pkgs_url')

        if not all(key in details for key in required_instance_keys):
            raise CIError('instance details do not contain all required keys')

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
        request = self.task_info["request"][0]
        try:
            git_hash = re.search("#[^']*", request).group()[1:]
            component = re.search("/rpms/[^?]*", request).group()[6:]
        except AttributeError:
            return None

        # get git commit html
        for url in self.dist_git_commit_urls:
            url = url.format(component=component, commit=git_hash)

            try:
                _, content = fetch_url(url, logger=self.logger)
                return BeautifulSoup(content, 'html.parser')

            except CIError:
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
            raise CIError("could not find 'branch-deco' class in html output of cgit, please inspect")

    @cached_property
    def issuer(self):
        """
        :returns: issuer of brew task and in case of build from automation, returns issuer of git commit
        """
        owner_id = self.task_info["owner"]
        if owner_id in self.automation_user_ids:
            return self.owner

        self.info("Automation user detected, need to get git commit issuer")
        if self._parsed_commit_html is None:
            raise CIError('could not find git commit issuer')

        issuer = self._parsed_commit_html.find(class_='commit-info').find('td')
        issuer = re.sub(".*lt;(.*)@.*", "\\1", str(issuer))

        return issuer

    @cached_property
    def rhel(self):
        """
        :returns: major version of RHEL
        """
        return re.sub(".*rhel-(\\d+).*", "\\1", self.target)


class Koji(Module):
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
            'help': 'Initialize from task ID',
            'type': int,
        },
        'build-id': {
            'help': 'Initialize from build ID',
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
        },
    }

    required_options = ['url', 'pkgs-url', 'web-url']
    shared_functions = ['task']

    def __init__(self, *args, **kwargs):
        super(Koji, self).__init__(*args, **kwargs)

        self.instance = None
        self.task_instance = None

    def _init_task(self, task_id, wait_timeout=None, details=None, task_class=None):
        task_class = task_class or KojiTask
        self.info('task-class: {}'.format(task_class))

        full_details = dict_update({
            'session': self.instance,
            'url': self.option('url'),
            'pkgs_url': self.option('pkgs-url'),
            'web_url': self.option('web-url'),
        }, details or {})

        self.task_instance = task_class(full_details, task_id, self.unique_name, logger=self.logger,
                                        wait_timeout=wait_timeout)

        self.info(self.task_instance.full_name)

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
            builds = [self.instance.getBuild(build_id)]
        elif nvr:
            builds = [self.instance.getBuild(nvr)]
        elif name:
            builds = self.instance.listTagged(tag, package=name)
        else:
            # no task to find, just continue without initialization
            # it will be expected that user inits task via the shared function
            return None

        if builds[0] and 'task_id' in builds[0] and builds[0]['task_id']:
            return builds[0]['task_id']

        raise CIError('could not find a valid build according to given details')

    def task(self, task_id=None):
        """
        Return a KojiTask or a BrewTask instance. If task_id passed, initialize KojiTask/BrewTask instance
        from it first.

        :param int task_id: ID of the task to process
        :returns: :py:class:`KojiTask` or `BrewTask` instance
        """
        if task_id:
            self._init_task(task_id)

        if self.task_instance:
            return self.task_instance

        raise CIError('no {} task ID specified'.format(self.unique_name))

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

        self.instance = koji.ClientSession(url)
        version = self.instance.getAPIVersion()
        self.info('connected to {} instance \'{}\' API version {}'.format(self.unique_name, url, version))

        task_id = self._find_task_id()
        if task_id:
            self._init_task(task_id, wait_timeout=wait_timeout)


class Brew(Koji, Module):
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

    def _init_task(self, task_id, wait_timeout=None, details=None, task_class=None):
        details = dict_update({}, {
            'automation_user_ids': self.option('automation-user-ids').split(','),
            'dist_git_commit_urls': self.option('dist-git-commit-urls').split(',')
        }, details or {})

        super(Brew, self)._init_task(task_id, wait_timeout=wait_timeout, details=details, task_class=BrewTask)
