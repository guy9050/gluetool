import re
import koji
import libci
from libci import CIError
from libci.utils import cached_property

BREW_API_TOPURL = "http://download.eng.bos.redhat.com/brewroot"
BREW_WEB_URL = 'https://brewweb.engineering.redhat.com/brew/'


class BrewTask(object):
    """
    This class provides details about a brew task resolved from internal Brew
    instance.
    """
    def __init__(self, module, brew_task_id, session):
        self._module = module
        self.task_id = brew_task_id
        self.brew = session

    @cached_property
    def task_info(self):
        task_info = self.brew.getTaskInfo(self.task_id, request=True)
        if not task_info:
            raise CIError("brew task '{}' not found".format(self.task_id))

        return task_info

    @cached_property
    def owner(self):
        owner_id = self.task_info["owner"]
        return self.brew.getUser(owner_id)["name"]

    @cached_property
    def target(self):
        try:
            target = self.task_info["request"][1]
        except IndexError:
            raise CIError('invalid build task id')
        return BrewBuildTarget(target, session=self.brew)

    @cached_property
    def scratch(self):
        try:
            if "scratch" in self.task_info["request"][2]:
                return self.task_info["request"][2]["scratch"]
        except TypeError:
            raise CIError('invalid build task id')
        return False

    @cached_property
    def url(self):
        return "{0}/taskinfo?taskID={1}".format(BREW_WEB_URL, self.task_id)

    @cached_property
    def latest(self):
        builds = self.brew.listTagged(self.target.destination_tag, None, True, latest=2, package=self.name)
        if self.scratch:
            latest = builds[0]["nvr"] if builds else None
        else:
            latest = builds[1]["nvr"] if builds and len(builds) > 1 else None

        if not latest:
            self._module.info('could not find latest released package from brew')

        return latest

    @cached_property
    def srcrpm(self):
        base_url = "{0}/work".format(BREW_API_TOPURL)

        if self.task_info['state'] != koji.TASK_STATES["CLOSED"]:
            raise CIError("Brew task [%s] is not a successfully completed task" % self.task_id)

        # For standard (non-scratch) builds, we may fetch an associated build and dig info from it
        builds = self.brew.listBuilds(taskID=self.task_id)
        if len(builds) == 1:
            build = builds[0]
            url = "{0}/packages/%s/%s/%s/src/%s.src.rpm".format(BREW_API_TOPURL)
            return url % (build["package_name"], build["version"], build["release"], build["nvr"])

        # For scratch build, there is no associated build and so we need to dig deeper
        if self.task_info['method'] == 'build':
            tasks = self.brew.listTasks(opts={'parent': self.task_id, 'method': 'buildArch',
                                              'state': [koji.TASK_STATES['CLOSED']], 'decode': True})
        elif self.task_info['method'] == 'buildArch':
            tasks = [self.task_info]
        else:
            raise CIError('brew task [%i] is not a build or buildArch task' % self.task_id)

        # Gather list of files for each (sub-)task. We'll end up with this list:
        #  [(task1, file1), (task1, file2), ..., (taskN, fileM)]
        tasks_outputs = []
        for task in tasks:
            tasks_outputs += [(task, filename) for filename in self.brew.listTaskOutput(task['id'])]

        if not any(tasks_outputs):
            msg = "no artifacts found for the task '{}', builds already gone for scratch build?".format(self.task_id)
            self._module.warn(msg)

            raise CIError("no brew artifacts found for the task '{}'".format(self.task_id))

        for task, filename in tasks_outputs:
            if not filename.endswith('.src.rpm'):
                continue

            base_path = koji.pathinfo.taskrelpath(task['id'])
            return "/".join([base_url, base_path, filename])

        raise CIError("Source RPM not found in Brew task [%s]." % self.task_id)

    @cached_property
    def nvr(self):
        return ".".join(self.srcrpm.split("/")[-1].split(".")[:-2])

    @cached_property
    def name(self):
        return "-".join(self.nvr.split("-")[:-2])

    @cached_property
    def version(self):
        return self.nvr.split("-")[-2]

    @cached_property
    def release(self):
        return self.nvr.split("-")[-1]

    @cached_property
    def full_name(self):
        msg = ["task '{}'".format(self.task_id)]
        if self.scratch:
            msg.append("scratch")
        msg.append("build '{}'".format(self.nvr))
        msg.append("destination tag '{}'".format(self.target.destination_tag))
        return ' '.join(msg)

    @cached_property
    def short_name(self):
        return "{t.task_id}:{scratch}{t.nvr}".format(t=self, scratch='S:' if self.scratch else '')


class BrewBuildTarget(object):
    def __init__(self, target_name, session):
        self.target = target_name
        self.brew = session

    @cached_property
    def destination_tag(self):
        try:
            return self.brew.getBuildTarget(self.target)["dest_tag_name"]
        except TypeError:
            raise CIError('invalid build task id')

    @cached_property
    def is_rhscl(self):
        return self.target[:6] == "rhscl-"

    @cached_property
    def is_dts(self):
        return self.target[:11] == "devtoolset-"

    @cached_property
    def is_staging(self):
        return self.target.startswith("staging-")

    @cached_property
    def rhel(self):
        """Gets major version of RHEL"""
        return re.sub(".*rhel-(\\d+).*", "\\1", self.target)

    @cached_property
    def rhscl_ver(self):
        if self.is_rhscl:
            return re.sub("rhscl-([^-]*).*", "\\1", self.target)
        else:
            raise CIError("build target '{}' is not an RHSCL target".format(self.target))

    @cached_property
    def dts_ver(self):
        if self.is_dts:
            return re.sub("devtoolset-([^-]*).*", "\\1", self.target)
        else:
            raise CIError("build target '{}' is not a DTS target".format(self.target))

    @cached_property
    def collection(self):
        if self.is_rhscl:
            return re.sub("rhscl-[^-]*-(.*)-rhel.*", "\\1", self.target)
        elif self.is_dts:
            return re.sub("(devtoolset-[^.-]*).*", "\\1", self.target)
        else:
            raise CIError("build target '{}' is not a RHSCL target".format(self.target))

    @staticmethod
    def is_extras_target(target):
        return target.startswith("extras")


class CIBrew(libci.Module):
    """Provide connection to Brew via koji python module"""

    name = 'brew'
    description = 'Connect to Brew instance via koji python module'
    requires = 'jenkinsapi'

    brew_task_instance = None

    options = {
        'url': {
            'help': 'Brew API server URL',
        },
        'id': {
            'help': 'Brew task ID',
            'type': int,
        }
    }
    required_options = ['url', 'id']

    shared_functions = ['brew_task']

    def brew_task(self):
        """ return a BrewTask instance of passed task_id """
        return self.brew_task_instance

    def execute(self):
        url = self.option('url')
        task_id = self.option('id')

        brew_instance = koji.ClientSession(url)
        version = brew_instance.getAPIVersion()
        self.info('connected to brew instance \'{}\' API version {}'.format(url, version))

        # print information about the task
        self.brew_task_instance = BrewTask(self, task_id, brew_instance)
        self.info(self.brew_task_instance.full_name)
