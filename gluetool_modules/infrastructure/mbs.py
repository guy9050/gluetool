import collections
import requests

import gluetool
from gluetool.utils import cached_property
from gluetool.log import log_dict

#: Information about task architectures.
#:
#: :ivar list(str) arches: List of architectures.
TaskArches = collections.namedtuple('TaskArches', ['arches'])


class MBSApi(object):

    def __init__(self, mbs_api_url, mbs_ui_url, module):
        self.mbs_api_url = mbs_api_url
        self.mbs_ui_url = mbs_ui_url
        self.module = module

    def _get_json(self, location):
        url = '{}/{}'.format(self.mbs_api_url, location)

        self.module.debug('[MBS API]: {}'.format(url))

        try:
            output = requests.get(url).json()
        except Exception:
            raise gluetool.GlueError('Unable to get: {}'.format(url))

        log_dict(self.module.debug, '[MBS API] output', output)
        return output

    def get_module_build(self, build_id):
        return self._get_json('module-build-service/1/module-builds/{}'.format(build_id))

    def get_build_ui_url(self, build_id):
        return '{}/module/{}'.format(self.mbs_ui_url, build_id)


class MBSTask(object):
    # pylint: disable=too-few-public-methods

    ARTIFACT_NAMESPACE = 'redhat-module'

    def __init__(self, build_id, module):
        # pylint: disable=invalid-name
        self.id = build_id

        self.module = module

        mbs_api = module.mbs_api()

        build_info = mbs_api.get_module_build(build_id)

        self.name = build_info['name']
        self.component = self.name
        self.stream = build_info['stream']
        self.version = build_info['version']
        self.context = build_info['context']
        self.issuer = build_info['owner']
        self.nsvc = '{}:{}:{}:{}'.format(self.name, self.stream, self.version, self.context)
        # `nvr` is often used as unique id of task (e.g. in mail notifications)
        # so this task sets `nvr` too, yet its value is not actually 'name', 'version' and 'release',
        # but 'name', 'stream', 'version' and 'context'
        self.nvr = self.nsvc
        # set by param for now
        self.target = self.module.option('target')

        # this string identifies component in static config file
        self.component_id = '{}:{}'.format(self.name, self.stream)

    @cached_property
    def task_arches(self):
        """
        :rtype: TaskArches
        :return: information about arches the task was building for
        """

        return TaskArches([self.module.option('arches')])

    @cached_property
    def url(self):
        return self.module.mbs_api().get_build_ui_url(self.id)


class MBS(gluetool.Module):
    name = 'mbs'
    description = 'Provides information about MBS (Module Build Service) artifact'

    options = {
        'mbs-ui-url': {
            'help': 'URL of mbs ui server.',
            'type': str
        },
        'mbs-api-url': {
            'help': 'URL of mbs api server.',
            'type': str
        },
        'build-id': {
            'help': 'MBS id',
            'type': str
        },
        'target': {
            'help': 'Value for property target (default: %(default)s).',
            'type': str,
            'default': 'module-rhel8'
        },
        'arches': {
            'help': 'Value for property arches (default: %(default)s).',
            'type': str,
            'default': 'x86_64'
        }
    }

    required_options = ('mbs-api-url', 'build-id')

    shared_functions = ['primary_task', 'tasks', 'mbs_api']

    def __init__(self, *args, **kwargs):
        super(MBS, self).__init__(*args, **kwargs)
        self.task = None
        self._tasks = None

    def primary_task(self):
        return self.task

    def tasks(self):
        return self._tasks

    @property
    def eval_context(self):
        # pylint: disable=unused-variable
        __content__ = {  # noqa
            'ARTIFACT_TYPE': """
                             Type of the artifact, ``mbs-build`` in the case of ``mbs`` module.
                             """,
            'BUILD_TARGET': """
                            Build target of the primary task, as known to Koji/Beaker.
                            """,
            'PRIMARY_TASK': """
                            Primary task, represented as ``MBSTask`` instance.
                            """,
            'TASKS': """
                     List of all tasks known to this module instance.
                     """
        }

        primary_task = self.primary_task()

        if not primary_task:
            self.warn('No primary task available, cannot pass it to eval_context', sentry=True)
            return {}

        return {
            # common for all artifact providers
            'ARTIFACT_TYPE': primary_task.ARTIFACT_NAMESPACE,
            'BUILD_TARGET': primary_task.target,
            'PRIMARY_TASK': primary_task,
            'TASKS': self.tasks()
        }

    @cached_property
    def _mbs_api(self):
        return MBSApi(self.option('mbs-api-url'), self.option('mbs-ui-url'), self)

    def mbs_api(self):
        return self._mbs_api

    def execute(self):
        build_id = self.option('build-id')

        self.task = MBSTask(build_id, self)
        self._tasks = [self.task]

        self.info('Initialized with {}: {} ({})'.format(self.task.id, self.task.nsvc, self.task.url))
