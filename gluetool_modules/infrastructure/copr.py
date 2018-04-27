from urllib2 import urlopen
import json
import re
import collections
from rpmUtils.miscutils import splitFilename

import gluetool
from gluetool import SoftGlueError
from gluetool.utils import cached_property
from gluetool.log import format_dict


class NotBuildTaskError(SoftGlueError):
    def __init__(self, build_id):
        super(NotBuildTaskError, self).__init__('Build task not found')

        self.build_id = build_id


#: Information about task architectures.
#:
#: :ivar list(str) arches: List of architectures.
TaskArches = collections.namedtuple('TaskArches', ['arches'])


class CoprTask(object):
    # pylint: disable=too-few-public-methods

    ARTIFACT_NAMESPACE = 'copr-build'

    def __init__(self, message, module):
        self.build_id = message['build']
        self.status = message['status']
        self.status_int = message['status_int']
        self.component = splitFilename(message['package'])[0]
        self.target = message['chroot']
        self.nvr = message['package']
        self.builder = message['builder']
        self.owner = message['owner']
        self.issuer = message['submitter']
        self.project = message['copr']
        self.artifact_id = format_dict(message)
        # this string identifies component in static config file
        self.component_id = '{}/{}/{}'.format(self.owner, self.project, self.component)

        self.module = module

    @cached_property
    def rpm_urls(self):
        url = "{}/api_2/build_tasks?build_id={}".format(self.module.option('copr_url'), self.build_id)

        build_tasks_json = urlopen(url).read()
        try:
            build_tasks = json.loads(build_tasks_json)
        except ValueError:
            raise NotBuildTaskError(self.build_id)

        for build_task in build_tasks['build_tasks']:
            build_task = build_task['build_task']

            if build_task['chroot_name'] == self.target:
                result_dir_url = build_task['result_dir_url']

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
        'copr_url': {
            'help': 'Url of Copr build server',
            'type': str
        },
        'init_message': {
            'help': 'Json message sent by copr build system',
            'type': str
        }
    }

    required_options = ('copr_url',)

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
        Provides informations about copr artifact.

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

        if self.option('init_message'):
            self.info("Parameter 'init_message' is used to init copr module.")
            self.task = CoprTask(gluetool.utils.from_json(self.option('init_message')), self)

        else:
            self.require_shared('trigger_message')
            self.info("Shared function 'trigger_message' is used to init copr module.")
            self.task = CoprTask(self.shared('trigger_message'), self)
