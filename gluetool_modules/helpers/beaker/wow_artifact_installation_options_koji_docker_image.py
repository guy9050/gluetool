import gluetool
from gluetool.log import log_dict


class WowArtifactIntallationOptionsKojiDockerImage(gluetool.Module):
    """
    Provides options for ``workflow-tomorrow`` which, when passed to ``wow``, enhance the final job
    with steps necessary to install the Docker image built by the Koji/Brew task under the test.
    """

    name = 'wow-artifact-installation-options-koji-docker-image'
    description = 'Create options for ``workflow-options`` to install the Docker image from a Koji/Brew task.'
    shared_functions = ('wow_artifact_installation_options',)

    # pylint: disable=invalid-name
    def wow_artifact_installation_options(self):
        """
        Return list of ``workflow-tomorrow`` options to install the Docker image from a Koji/Brew tasks.

        :rtype: list(str)
        """

        self.require_shared('primary_task')

        task = self.shared('primary_task')

        image_archives = [archive for archive in task.build_archives if archive['btype'] == 'image']

        if not image_archives:
            raise gluetool.GlueError('No "image" archive in task {}'.format(self.shared('primary_task').id))

        install_docker_image_params = ' '.join([
            '{}="{}"'.format(param, value) for param, value in {
                'IMAGE_URL': image_archives[0]['image_url']
            }.iteritems()
        ])

        options = [
            '--init-task=/tools/toolchain-common/Install/configure-extras-repo',
            '--init-task=/examples/sandbox/emachado/enable-docker',
            '--init-task={} /examples/sandbox/emachado/install-docker-image'.format(install_docker_image_params),
            '--init-task=/examples/sandbox/emachado/install-docker-test-config'
        ]

        log_dict(self.debug, 'wow options for Docker artifact installation', options)

        return options
