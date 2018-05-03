import gluetool


class BrewBuildOptions(gluetool.Module):
    """
    Create options for ``/distribution/install/brew-build task``.

    This task is being used to install both Koji and Brew builds on both Beaker and OpenStack guests.
    Its actual involvement in the process may differ but its inputs are still the same and it makes
    sense to construct its options just once, and use them by different pipelines as they wish to.
    """

    name = 'brew-build-task-params'
    description = 'Create options for /distribution/install/brew-build task.'

    options = {
        'install-task-not-build': {
            'help': 'Try to install SUT using brew task ID as a referrence, instead of the brew build ID.',
            'action': 'store_true',
            'default': False
        },
        'install-rpms-blacklist': {
            # pylint: disable=line-too-long
            'help': 'Regexp pattern (compatible with ``egrep``) - when installing build, matching packages will not be installed.',
            'type': str,
            'default': ''
        },
        'install-method': {
            'help': 'Yum method to use for installation (default: ``install``).',
            'type': str,
            'default': 'install'
        }
    }

    shared_functions = ('brew_build_task_params',)

    def brew_build_task_params(self):
        """
        Return mapping with options for ``/distribution/install/brew-build``, to install currently known artifacts.
        """

        self.require_shared('primary_task', 'tasks')

        # temporary holders of options
        tasks = []
        builds = []

        if self.option('install-task-not-build'):
            self.debug('asked to install by task ID')

            tasks = [task.id for task in self.shared('tasks')]

        else:
            for task in self.shared('tasks'):
                if task.scratch:
                    self.debug('task {} is a scratch build, using task ID for installation'.format(task.id))

                    tasks.append(task.id)

                else:
                    self.debug('task {} is a regular task, using build ID for installation'.format(task.id))

                    builds.append(task.build_id)

        options = {
            'METHOD': self.option('install-method'),
            'SERVER': self.shared('primary_task').ARTIFACT_NAMESPACE,
            'RPM_BLACKLIST': self.option('install-rpms-blacklist')
        }

        if tasks:
            options['TASKS'] = ' '.join([str(i) for i in tasks])

        if builds:
            options['BUILDS'] = ' '.join([str(i) for i in builds])

        return options
