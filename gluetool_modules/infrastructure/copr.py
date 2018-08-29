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


class CoprApi(object):

    def __init__(self, copr_url, module):
        self.copr_url = copr_url
        self.module = module

    def _get_json(self, url, label):
        url = '{}/{}'.format(self.copr_url, url)

        self.module.debug('[copr API] {}: {}'.format(label, url))

        try:
            output = requests.get(url).json()
        except Exception:
            raise gluetool.GlueError('Unable to get: {}'.format(url))

        log_dict(self.module.debug, '[copr API] {} output'.format(label), output)
        return output

    def get_href(self, href):
        return self._get_json(href, '')

    def get_build_info(self, build_id):
        return self._get_json('api_2/builds/{}'.format(build_id), 'build info')

    def get_build_task_info(self, build_id, chroot_name):
        return self._get_json('api_2/build_tasks/{}/{}'.format(build_id, chroot_name), 'build tasks info')


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
        self.task_id = task_id

        self.module = module

        copr_api = module.copr_api()

        build_info = copr_api.get_build_info(task_id.build_id)
        self._build = build_info['build']

        project_info = copr_api.get_href(build_info['_links']['project']['href'])
        self._project = project_info['project']

        build_tasks_info = copr_api.get_build_task_info(task_id.build_id, task_id.chroot_name)
        self._build_task = build_tasks_info['build_task']

        self.status = self._build_task['state']
        self.component = self._build['package_name']
        self.target = task_id.chroot_name
        self.nvr = '{}-{}'.format(self._build['package_name'], self._build['package_version'])
        self.owner = self._project['owner']
        self.issuer = self._build.get('submitter', 'UNKNOWN-COPR-ISSUER')
        self.project = self._project['name']
        # this string identifies component in static config file
        self.component_id = '{}/{}/{}'.format(self.owner, self.project, self.component)

    @cached_property
    def rpm_urls(self):
        result_dir_url = '{}/builder-live.log'.format(self._build_task['result_dir_url'])
        self.module.debug('result_dir_url: {}'.format(result_dir_url))
        try:
            builder_live_log = requests.get(result_dir_url).text
        except Exception:
            raise gluetool.GlueError('Unable to get: {}'.format(result_dir_url))

        log_dict(self.module.debug, 'builder live log', builder_live_log)
        rpm_names = re.findall(r'Wrote: /builddir/build/RPMS/(.*\.rpm)', builder_live_log)

        return ['{}/{}'.format(self._build_task['result_dir_url'], rpm_name) for rpm_name in rpm_names]

    @cached_property
    def task_arches(self):
        """
        :rtype: TaskArches
        :return: information about arches the task was building for
        """

        return TaskArches([self.target.split('-')[-1]])

    @cached_property
    def full_name(self):
        """
        String with human readable task details. Used for slightly verbose representation e.g. in logs.

        :rtype: str
        """

        name = [
            "package '{}'".format(self.component),
            "build '{}'".format(self.task_id.build_id),
            "target '{}'".format(self.task_id.chroot_name)
        ]

        return ' '.join(name)


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

    required_options = ('copr-url', 'task-id')

    shared_functions = ['primary_task', 'tasks', 'copr_api']

    def __init__(self, *args, **kwargs):
        super(Copr, self).__init__(*args, **kwargs)
        self.task = None
        self._tasks = None

    def primary_task(self):
        return self.task

    def tasks(self, task_ids=None):

        if not task_ids:
            return self._tasks

        self._tasks = []

        for task_id in task_ids:
            build_id, chroot_name = [s.strip() for s in task_id.split(':')]
            self._tasks.append(CoprTask(BuildTaskID(int(build_id), chroot_name), self))

        return self._tasks

    @property
    def eval_context(self):
        # pylint: disable=unused-variable
        __content__ = {  # noqa
            'ARTIFACT_TYPE': """
                             Type of the artifact, ``copr-build`` in the case of ``copr`` module.
                             """,
            'BUILD_TARGET': """
                            Build target of the primary task, as known to Koji/Beaker.
                            """,
            'NVR': """
                   NVR of the primary task.
                   """,
            'PRIMARY_TASK': """
                            Primary task, represented as ``CoprTask`` instance.
                            """,
            'TASKS': """
                     List of all tasks known to this module instance.
                     """
        }

        primary_task = self.primary_task()

        return {
            # common for all artifact providers
            'ARTIFACT_TYPE': primary_task.ARTIFACT_NAMESPACE,
            'BUILD_TARGET': primary_task.target,
            'NVR': primary_task.nvr,
            'PRIMARY_TASK': primary_task,
            'TASKS': self.tasks()
        }

    @cached_property
    def _copr_api(self):
        return CoprApi(self.option('copr-url'), self)

    def copr_api(self):
        return self._copr_api

    def execute(self):
        build_id, chroot_name = [s.strip() for s in self.option('task-id').split(':')]

        self.debug('build_id: {}'.format(build_id))
        self.debug('chroot_name {}'.format(chroot_name))

        self.task = CoprTask(BuildTaskID(int(build_id), chroot_name), self)
        self._tasks = [self.task]
