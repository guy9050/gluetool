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

    def wow_artifact_installation_options(self):
        """
        Return list of ``workflow-tomorrow`` options to install the Docker image from a Koji/Brew tasks.

        :rtype: list(str)
        """

        self.require_shared('primary_task')

        task = self.shared('primary_task')

        # Need to finish: we have to chose the correct architecture. Right now, we have just
        # a single archive or registry URL, and we have to somehow tell Beaker boxes "you are
        # going to install *this* image" - I have no idea how to do that :/ Not enough information
        # in URLs to allow e.g. $(arch) trick.

        archive_url = ''
        registry_url = ''

        # If there is a build, there should be an archive in that build, with a link to
        # tar & gzipped image.
        if task.has_build:
            # Find image archives, we'll grab the first one when constructing final XML
            image_archives = [
                archive for archive in task.build_archives if archive['btype'] == 'image'
            ]

            if not image_archives:
                raise gluetool.GlueError('No "image" archive in task {}'.format(task.id))

            archive_url = image_archives[0]['image_url']

        else:
            # Otherwise, take one of the repositories, and use its URL.
            if not task.image_repositories:
                raise gluetool.GlueError('No image repositories in task {}'.format(task.id))

            registry_url = task.image_repositories[0].url

        install_docker_image_params = ' '.join([
            '{}="{}"'.format(param, value) for param, value in {
                'IMAGE_ARCHIVE_URL': archive_url,
                'IMAGE_REGISTRY_URL': registry_url
            }.iteritems()
        ])

        options = [
            '--init-task=/tools/toolchain-common/Install/configure-extras-repo',
            '--init-task=/examples/sandbox/emachado/enable-docker',
            '--init-task=/examples/sandbox/emachado/remove-docker-images',
            '--init-task={} /examples/sandbox/emachado/install-docker-image'.format(install_docker_image_params),
            '--init-task=/examples/sandbox/emachado/install-docker-test-config'
        ]

        log_dict(self.debug, 'wow options for Docker artifact installation', options)

        return options
