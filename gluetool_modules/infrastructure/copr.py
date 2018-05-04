from urllib2 import urlopen
import re
import collections
import requests

import gluetool
from gluetool import SoftGlueError
from gluetool.utils import cached_property
from gluetool.log import log_dict


class NotBuildTaskError(SoftGlueError):
    def __init__(self, build_id):
        super(NotBuildTaskError, self).__init__('Build task not found')

        self.build_id = build_id


#: Information about task architectures.
#:
#: :ivar list(str) arches: List of architectures.
TaskArches = collections.namedtuple('TaskArches', ['arches'])


class BuildTaskID(object):
    """
    Build task ID consist of build ID and chroot name. This class covers both values and provides them like
    one string, with following format: '[build_id]:[chroot_name]'
    """

    # pylint: disable=too-few-public-methods

    def __init__(self, build_id, chroot_name):
        self.build_id = build_id
        self.chroot_name = chroot_name

    def __str__(self):
        return '{}:{}'.format(self.build_id, self.chroot_name)

    def __repr__(self):
        return self.__str__()


class CoprTask(object):
    """
    Covers copr build task and provides all necessary information about it.

    :param BuildTaskID task_id: Task id used to initialization.
    :param gluetool.Module module: Reference to parent's module (used eg. for logging).
    """

    # pylint: disable=too-few-public-methods

    ARTIFACT_NAMESPACE = 'copr-build'

    def __init__(self, task_id, module):
        # as an "official ID", use string representation - some users might be confused by the object,
        # despite it has proper __str__ and __repr__
        # pylint: disable=invalid-name
        self.id = str(task_id)
        self._task_id = task_id

        self.module = module

        copr_url = module.option('copr-url')

        build_info_url = '{}/api_2/builds/{}'.format(copr_url, task_id.build_id)
        self.module.debug('build_info_url: {}'.format(build_info_url))
        try:
            build_info = requests.get(build_info_url).json()
        except Exception:
            raise gluetool.GlueError('Unable to get: {}'.format(build_info_url))

        project_info_url = '{}/{}'.format(copr_url, build_info['_links']['project']['href'])
        self.module.debug('project_info_url: {}'.format(project_info_url))
        try:
            project_info = requests.get(project_info_url).json()
        except Exception:
            raise gluetool.GlueError('Unable to get: {}'.format(project_info_url))

        build_tasks_info_url = '{}/api_2/build_tasks/{}/{}'.format(copr_url, task_id.build_id, task_id.chroot_name)
        self.module.debug('build_tasks_info_url: {}'.format(build_tasks_info_url))
        try:
            build_tasks_info = requests.get(build_tasks_info_url).json()
        except Exception:
            raise gluetool.GlueError('Unable to get: {}'.format(build_tasks_info_url))

        log_dict(self.debug, 'build info', build_info)
        self._build = build_info['build']

        log_dict(self.debug, 'project info', project_info)
        self._project = project_info['project']

        log_dict(self.debug, 'build tasks info', build_tasks_info)
        self._build_task = build_tasks_info['build_task']

        self.status = self._build_task['state']
        self.component = self._build['package_name']
        self.target = task_id.chroot_name
        self.nvr = '{}-{}'.format(self._build['package_name'], self._build['package_version'])
        self.owner = self._project['owner']
        self.issuer = self._build['submitter']
        self.project = self._project['name']
        # this string identifies component in static config file
        self.component_id = '{}/{}/{}'.format(self.owner, self.project, self.component)

    @cached_property
    def rpm_urls(self):
        result_dir_url = self._build_task['result_dir_url']
        builder_live_log = urlopen('{}/builder-live.log'.format(result_dir_url)).read()

        rpm_names = re.findall(r'Wrote: /builddir/build/RPMS/(.*\.rpm)', builder_live_log)

        return ['{}/{}'.format(result_dir_url, rpm_name) for rpm_name in rpm_names]

    @cached_property
    def task_arches(self):
        """
        :rtype: TaskArches
        :return: information about arches the task was building for
        """

        return TaskArches([self.target.split('-')[-1]])


class Copr(gluetool.Module):

    name = 'copr'
    description = 'Copr'

    options = {
        'copr-url': {
            'help': 'Url of Copr build server',
            'type': str
        },
        'task-id': {
            'help': 'Copr build task ID, in a form of ``build-id:chroot-name``.',
            'type': str
        }
    }

    required_options = ('copr-url',)

    shared_functions = ['primary_task', 'tasks']

    def __init__(self, *args, **kwargs):
        super(Copr, self).__init__(*args, **kwargs)
        self.task = None

    def primary_task(self):
        return self.task

    def tasks(self):
        return [self.task]

    @property
    def eval_context(self):
        """
        Provides information about copr artifact.

        Provides following variables: BUILD_TARGET, PRIMARY_TASK, TASKS, NVR

        :rtype: dict
        """

        primary_task = self.primary_task()

        return {
            # common for all artifact providers
            'ARTIFACT_TYPE': primary_task.ARTIFACT_NAMESPACE,
            'BUILD_TARGET': primary_task.target,
            'NVR': primary_task.nvr,
            'PRIMARY_TASK': primary_task,
            'TASKS': self.tasks()
        }

    def execute(self):
        if self.option('task-id'):
            self.info("Option 'task-id' is used to init copr module.")
            build_id, chroot_name = [s.strip() for s in self.option('task-id').split(':')]

        else:
            self.require_shared('trigger_message')
            self.info("Shared function 'trigger_message' is used to init copr module.")
            message = self.shared('trigger_message')

            build_id = message['build']
            chroot_name = message['chroot']

        self.debug('build_id: {}'.format(build_id))
        self.debug('chroot_name {}'.format(chroot_name))

        self.task = CoprTask(BuildTaskID(int(build_id), chroot_name), self)
