import gluetool


class CoprTask(object):
    # pylint: disable=too-few-public-methods
    def __init__(self, message):
        self.status = message['status']
        self.status_int = message['status_int']
        self.component = message['copr']
        self.target = message['chroot']
        self.nvr = message['package']
        self.builder = message['builder']
        self.owner = message['owner']
        self.issuer = message['submitter']


class Copr(gluetool.Module):

    name = 'copr'
    description = 'Copr'

    shared_functions = ['primary_task', 'tasks', 'artifact_context']

    def __init__(self, *args, **kwargs):
        super(Copr, self).__init__(*args, **kwargs)
        self.task = None

    def primary_task(self):
        return self.task

    def tasks(self):
        return [self.task]

    def artifact_context(self):
        """
        Provides informations about copr artifact, that are used for rules evaluations.
        Provides following variables: BUILD_TARGET, PRIMARY_TASK, TASKS, NVR

        :rtype: dict
        """

        primary_task = self.primary_task()

        return {
            'BUILD_TARGET': primary_task.target,
            'PRIMARY_TASK': primary_task,
            'TASKS': self.tasks(),
            'NVR': primary_task.nvr
        }

    def execute(self):
        self.require_shared('trigger_message')
        self.task = CoprTask(self.shared('trigger_message'))
