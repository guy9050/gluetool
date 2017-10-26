import random
import time

import libci
from libci.log import log_dict


DEFAULT_NAME = 'citool'


def rand_id():
    return random.randint(0, 999999999)


class Image(object):
    """
    Thin wrapper around Docker's image instance, providing few helper methods.
    """

    @staticmethod
    def image_by_id(docker, image_id):
        return Image(docker.images.get(image_id))

    @staticmethod
    def image_by_name(docker, image_name):
        return Image(docker.images.get(image_name), name=image_name)

    def __init__(self, image, name=None):
        self._image = image
        self.name = name

        self.full_id = image.id.split(':')[1]
        self.short_id = image.short_id.split(':')[1]

    def __repr__(self):
        name = self.name if self.name is not None else '<unknown name>'

        return "'{}' ({})".format(name, self.full_id)

    @property
    def attrs(self):
        self._image.reload()

        return self._image.attrs


# pylint: disable=abstract-method
class DockerGuest(libci.guest.Guest):
    """
    Guest backed by Docker containers and images.

    During its lifetime Docker guest can create many images, and to keep their names
    readable, each guest has its own "namespace". Docker image can be identified using
    different identificators:

    * SHA256 ID - it is unique, each image has one and only one ID. It's long and somewhat
      readable.
    * short ID - shortened variant of ID. Much better.
    * repository + tag - it looks like a "name", e.g. ``rhscl/devtoolset-4-toolchain-rhel7:latest``.
      ``latest`` is the tag, the rest is called repository. One image can have multiple "names"
      which share ID but differ in repository/tag.

    To identify images that belong to the guest we create a "namespace":

        ``<guest name>-<random string>/<base image ID>``

    This namespace then represents *repository* part of guest's image names, and each image
    gets a tag, representing a "generation" of the base image - with each change, generation
    is raised by one, and together with namespace creates a trail of unique, followable image
    names. This way we can referr to images using their IDs while we still can print somewhat
    readable names to follow in logs.

    Base image has usually completely different name but we add it into our namespace as a
    "generation zero" image.

    :param libci.ci.Module module: module that created this guest.
    :param str name: name of the guest.
    :param docker: connection to the Docker server.
    :param Image image: image to instantiate.
    """

    def __init__(self, module, name, docker, image):
        super(DockerGuest, self).__init__(module, name)

        self._docker = docker
        self._name = name

        # each guest has its own namespace where it keeps its private images
        self._namespace = '{}-{}/{}'.format(self.name, rand_id(), image.full_id)
        self._generation = 0

        self.debug("using namespace '{}'".format(self._namespace))

        # tag base image into our namespace
        image_name, namespace, tag = self._current_image

        self._docker.images.get(image.full_id).tag(repository=namespace, tag=tag)

        # and use it as the initial image
        self._image = Image.image_by_name(self._docker, image_name)
        self._container = None

        self.debug("base image '{}' tagged as guest image {}".format(image.name, self._image))

        # initialize lists of things we created for this guest, so we could clean up after it
        self._created_images = [self._image]
        self._created_containers = []

        self.debug("current image is {}".format(self._image))

    def __repr__(self):
        return '{}'.format(self._namespace)

    @property
    def supports_snapshot(self):
        return True

    @property
    def _current_image(self):
        """
        Return parts of current image.

        It's easily possible there is *no* current image but that's not important - it
        may be created and named using the parts this method returns.

        :returns: (full name, namespace, tag)
        """

        tag = 'gen{}'.format(self._generation)

        return ('{}:{}'.format(self._namespace, tag), self._namespace, tag)

    def _instantiate_container(self, creator, *args, **kwargs):
        """
        Create the container from the current image.

        :param creator: one of :py:func:`docker.containers.create` or :py:func:`docker.containers.run`.
        :param args: arguments for ``creator``.
        :param kwargs: keyword arguments for ``creator``.
        :returns: :py:mod:`docker`'s ``Container`` instance.
        """

        assert self._image is not None

        self.debug("creating a container from '{}'".format(self._image.name))

        container = creator(self._image.full_id, *args, **kwargs)

        self.debug("container is '{}'".format(container.id))

        self._container = container
        self._created_containers.append(container)

        return container

    def _create_container(self):
        """
        ``docker create`` analogue - create a container, but don't start it.

        :returns: :py:mod:`docker`'s ``Container`` instance.
        """

        return self._instantiate_container(self._docker.containers.create)

    def _run_container(self, cmd):
        """
        ``docker run`` anologue - create container and run a command in it.

        :param str: command to run.
        :returns: :py:mod:`docker`'s ``Container`` instance.
        """

        container = self._instantiate_container(self._docker.containers.create, command=cmd)

        try:
            container.start()

        # pylint: disable=broad-except
        except Exception as exc:
            error = exc

        else:
            error = None

        return container, error

    def _commit_container(self):
        assert self._container is not None

        container, self._container = self._container, None

        self.debug("committing the container '{}'".format(container.id))

        container.reload()
        assert container.status in ('created', 'exited'), \
            'Unexpected container status found {}'.format(container.status)

        self._generation += 1
        self.debug('generation raised to {}'.format(self._generation))

        image_name, namespace, tag = self._current_image
        container.commit(repository=namespace, tag=tag)

        self.debug("commited as image '{}'".format(image_name))

        self._image = Image.image_by_name(self._docker, image_name)
        self._created_images.append(self._image)

        self.debug("current image is {}".format(self._image))

    def _execute_shell(self, cmd, **kwargs):
        return libci.utils.run_command(cmd, logger=self.logger, **kwargs)

    def execute(self, cmd, **kwargs):
        self.debug("execute: '%s'" % (cmd))

        if self._container is not None:
            # there is container, left by some previous execute call. commit its image
            # and use it for our command

            self._commit_container()

        container, error = self._run_container(cmd)

        if error is None:
            # wait for it to finish the execution
            def _check_exited():
                container.reload()
                return container.status == 'exited'

            self.wait('container exits', _check_exited, tick=2)

        # refresh container's data - we don't expect them to change anymore, container
        # is finished
        container.reload()

        # construct process output package, just like run_command does
        stdout = container.logs(stdout=True, stderr=False, stream=False)
        stderr = container.logs(stdout=False, stderr=True, stream=False)

        log_dict(self.debug, 'container attributes', container.attrs)

        output = libci.utils.ProcessOutput(cmd, container.attrs['State']['ExitCode'], stdout, stderr, {})

        output.log(self.debug)

        if output.exit_code != 0:
            raise libci.CICommandError(cmd, output)

        return output

    def copy_to(self, src, dst, recursive=False, **kwargs):
        if recursive is False:
            self.warn("Cannot disable recursive behavior of 'cp' command")

        if self._container is None:
            self._create_container()

        return self._execute_shell(['docker', 'cp', src, '{}:{}'.format(self._container.id, dst)])

    def copy_from(self, src, dst, recursive=False, **kwargs):
        if recursive is False:
            self.warn("Cannot disable recursive behavior of 'cp' command")

        if self._container is None:
            self._create_container()

        return self._execute_shell(['docker', 'cp', '{}:{}'.format(self._container.id, src), dst])

    def create_snapshot(self):
        self.debug('creating a snapshot')

        if self._container is not None:
            self._commit_container()

        self.debug('snapshot is {}'.format(self._image))
        return self._image

    def restore_snapshot(self, snapshot):
        self.debug("restoring snapshot {}".format(snapshot))

        return self._module.guest_factory(self._module, '{}-{}'.format(self._name, rand_id()), self._docker, snapshot)

    def destroy(self):
        # reversing the lists does not seem to be necessary - newer images do not
        # depend on older ones, only containers must be removed before removing
        # the images they were created from.

        for container in self._created_containers:
            self.debug("removing container '{}'".format(container.id))
            container.remove()

        # We *must* refer to images using their names. Using the image SHA ID would work
        # for images we created by commiting containers. We didn't create the base image
        # we received from guest's creator, therefore we shouldn't remove it. Unfortunately,
        # the very first image we tagged into our namespace shares the ID with the base image
        # - we didn't create our initial image from the base one, we simply gave it another
        # name. Therefore we must remove the *name* and docker will simply remove the name
        # and leave the image itself untouched. For all images we created from containers
        # removing name will lead to removing the image as well - these images have no other
        # names that would keep them "alive".

        for image in self._created_images:
            self.debug("removing image {}".format(image))
            self._docker.images.remove(image=image.name)

    def setup(self, variables=None, **kwargs):
        # pylint: disable=arguments-differ

        variables = variables or {}

        if 'IMAGE_NAME' not in variables:
            variables['IMAGE_NAME'] = self._current_image

        super(DockerGuest, self).setup(variables=variables, **kwargs)


class DockerProvisioner(libci.Module):
    """
    Provision guests backed by docker containers.
    """

    name = 'docker-provisioner'
    description = 'Provision guests backed by docker containers.'

    options = [
        ('Direct provisioning', {
            'provision': {
                'help': 'Provision given number of guests',
                'metavar': 'COUNT',
                'type': int
            },
            'image': {
                'help': 'Force image name to be used.'
            },
            'setup-provisioned': {
                'help': "Setup guests after provisioning them. See 'guest-setup' module",
                'action': 'store_true'
            },
            'execute': {
                'help': 'Execute command in provisioned containers.'
            }
        })
    ]

    shared_functions = ['provision']

    def __init__(self, *args, **kwargs):
        super(DockerProvisioner, self).__init__(*args, **kwargs)

        self._guests = []

    def guest_factory(self, *args, **kwargs):
        """
        Create a docker guest, and add it to the list of guests. All arguments are passed
        directly to :py:class:`DockerGuest`.

        :rtype: DockerGuest
        :returns: new guest instance.
        """

        guest = DockerGuest(*args, **kwargs)
        self._guests.append(guest)

        return guest

    # pylint: disable=unused-argument
    def provision(self, count=1, name=DEFAULT_NAME, image=None, **kwargs):
        """
        Provision guests.

        :param int count: number of guests to provision.
        :param str name:
        """

        if count < 1:
            raise libci.CIError('You must provision at least one guest')

        if image is None:
            raise libci.CIError('You must specify docker image')

        docker = self.shared('docker')

        self.info("looking for image by name '{}'".format(image))

        image = Image.image_by_name(docker, image)
        self.info('image is {}'.format(image))

        return [self.guest_factory(self, name, docker, image) for _ in range(0, count)]

    def execute(self):
        random.seed(int(time.time()))

        if self.option('provision'):
            if not self.option('image'):
                raise libci.CIError('You must specify image when using direct provisioning')

            guests = self.provision(count=self.option('provision'), image=self.option('image'))

            if self.option('setup-provisioned'):
                for guest in guests:
                    guest.setup()

            if self.option('execute'):
                for guest in guests:
                    try:
                        output = guest.execute(self.option('execute'))

                    except libci.CICommandError as exc:
                        output = exc.output

                    output.log(self.info)

    def destroy(self, failure=None):
        for guest in self._guests:
            guest.destroy()
