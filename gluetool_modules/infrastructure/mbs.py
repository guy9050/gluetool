import requests

import gluetool
from gluetool.utils import cached_property
from gluetool.log import log_dict


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
        return '{}/modules/{}'.format(self.mbs_ui_url, build_id)


class MBSTask(object):
    # pylint: disable=too-few-public-methods

    ARTIFACT_NAMESPACE = 'mbs-build'

    def __init__(self, build_id, module):
        # pylint: disable=invalid-name
        self.id = build_id

        self.module = module

        mbs_api = module.mbs_api()

        build_info = mbs_api.get_module_build(build_id)

        self.name = build_info['name']
        self.stream = build_info['stream']
        self.version = build_info['version']
        self.owner = build_info['owner']
        # set by param for now
        self.target = self.module.option('target')

        # this string identifies component in static config file
        self.component_id = '{}:{}'.format(self.name, self.stream)


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
            'default': 'RHEL-8'
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

        self.info('Init using build {} ({})'.format(build_id, self._mbs_api.get_build_ui_url(build_id)))
