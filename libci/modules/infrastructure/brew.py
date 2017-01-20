import re
import koji
import libci
from libci import CIError

BREW_API_TOPURL = "http://download.eng.bos.redhat.com/brewroot"
BREW_WEB_URL = 'https://brewweb.engineering.redhat.com/brew/'


class BrewTask(object):
    """ Brew task class """
    def __init__(self, module, brew_task_id, session):
        self._module = module
        self.task_id = brew_task_id
        self._task_info = None
        self.brew = session
        self._srcrpm = None

    @property
    def task_info(self):
        if self._task_info is None:
            self._task_info = self.brew.getTaskInfo(self.task_id, request=True)
            if not self._task_info:
                raise CIError("brew task '{}' not found".format(self.task_id))

        return self._task_info

    @property
    def owner(self):
        owner_id = self.task_info["owner"]
        return self.brew.getUser(owner_id)["name"]

    @property
    def target(self):
        try:
            target = self.task_info["request"][1]
        except IndexError:
            raise CIError('invalid build task id')
        return BrewTarget(target, session=self.brew)

    @property
    def scratch(self):
        if "scratch" in self.task_info["request"][2]:
            return self.task_info["request"][2]["scratch"]

        return False

    @property
    def url(self):
        return "{0}/taskinfo?taskID={1}".format(BREW_WEB_URL, self.task_id)

    @property
    def latest(self):
        builds = self.brew.listTagged(self.target.tag, None, True, latest=2, package=self.name)
        if self.scratch:
            latest = builds[0]["nvr"] if builds else None
        else:
            latest = builds[1]["nvr"] if builds and len(builds) > 1 else None

        if not latest:
            self._module.info('could not find latest released package from brew')

        return latest

    @property
    def srcrpm(self):
        if self._srcrpm:
            return self._srcrpm

        base_url = "{0}/work".format(BREW_API_TOPURL)

        if self.task_info['state'] != koji.TASK_STATES["CLOSED"]:
            raise CIError("Brew task [%s] is not a successfully completed task" % self.task_id)

        # For standard (non-scratch) builds, we may fetch an associated build and dig info from it
        builds = self.brew.listBuilds(taskID=self.task_id)
        if len(builds) == 1:
            build = builds[0]
            url = "{0}/packages/%s/%s/%s/src/%s.src.rpm".format(BREW_API_TOPURL)
            self._srcrpm = url % (build["package_name"], build["version"], build["release"], build["nvr"])
            return self._srcrpm

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
            msg = "no artifacts found for the task [{}]".format(self.task_id)
            msg += ", builds already gone for scratch build?"

            raise CIError(msg)

        self._srcrpm = None

        for task, filename in tasks_outputs:
            if not filename.endswith('.src.rpm'):
                continue

            base_path = koji.pathinfo.taskrelpath(task['id'])
            self._srcrpm = "/".join([base_url, base_path, filename])
            break
        else:
            raise CIError("Source RPM not found in Brew task [%s]." % self.task_id)

        return self._srcrpm

    @property
    def nvr(self):
        return ".".join(self.srcrpm.split("/")[-1].split(".")[:-2])

    @property
    def name(self):
        return "-".join(self.nvr.split("-")[:-2])

    @property
    def version(self):
        return self.nvr.split("-")[-2]

    @property
    def release(self):
        return self.nvr.split("-")[-1]


class BrewTarget(object):
    def __init__(self, target_name, session):
        self.target = target_name
        self.brew = session

    @property
    def tag(self):
        try:
            return self.brew.getBuildTarget(self.target)["dest_tag_name"]
        except TypeError:
            raise CIError('invalid build task id')

    def is_rhscl(self):
        return self.target[:6] == "rhscl-"

    def is_dts(self):
        return self.target[:11] == "devtoolset-"

    def is_staging(self):
        return self.target.startswith("staging-")

    def rhel(self):
        """Gets major version of RHEL"""
        return re.sub(".*rhel-(\\d+).*", "\\1", self.target)

    def rhscl_ver(self):
        if self.is_rhscl():
            return re.sub("rhscl-([^-]*).*", "\\1", self.target)
        else:
            print "ERROR: rhscl_ver() is only for RHSCL targets"
            msg = "Called method BrewTarget.rhscl_ver() is only for rhscl targets. Run on target %s" % (self.target)
            raise CIError(msg)

    def dts_ver(self):
        if self.is_dts():
            return re.sub("devtoolset-([^-]*).*", "\\1", self.target)
        else:
            print "ERROR: dts_ver() is only for DTS targets"
            msg = "Called method BrewTarget.dts_ver() is only for dts targets. Run on target %s" % (self.target)
            raise CIError(msg)

    def collection(self):
        if self.is_rhscl():
            return re.sub("rhscl-[^-]*-(.*)-rhel.*", "\\1", self.target)
        elif self.is_dts():
            return re.sub("(devtoolset-[^.-]*).*", "\\1", self.target)
        else:
            print "ERROR: collection() is only for RHSCL"
            msg = "Called method BrewTarget.collection() is only for rhscl targets. Run on target %s" % (self.target)
            raise CIError(msg)

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

        self.brew_task_instance = BrewTask(self, task_id, brew_instance)
        # just test if connection found
        if self.brew_task_instance.task_info:
            pass
