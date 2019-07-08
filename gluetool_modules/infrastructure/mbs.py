import collections
import urllib

import re

from concurrent.futures import ThreadPoolExecutor, wait
import requests

# pylint: disable=no-name-in-module
from jq import jq

import gluetool
from gluetool.action import Action
from gluetool.utils import cached_property, normalize_multistring_option
from gluetool.log import log_dict

#: Information about task architectures.
#:
#: :ivar list(str) arches: List of architectures.
TaskArches = collections.namedtuple('TaskArches', ['arches'])

#: Information about MBS.
#:
#: :ivar str api_version: MBS API version.
#: :ivar str auth_method: MBS authentication method.
#: :ivar str version: MBS version.
MBSAbout = collections.namedtuple('MBSAbout', 'api_version, auth_method, version')

# regular expressions for nvr and nsvc of a module
NSVC_REGEX = re.compile(r'^([^:]*):([^:]*):([^:]*):([^:]*)$')
NVR_REGEX = re.compile(r'^(.*)-([^-]*)-([^\.]*)\.(.*)$')


def nsvc_from_string(nsvc):
    """
    Helper function to return a tuple of NSVC from a string.

    :param: str nsvc: NSVC string.
    :rtype: tuple
    :returns: Tuple of N, S, V, C.
    :raises: gluetool.GlueError if NSVC not valid.
    """
    try:
        return re.match(NSVC_REGEX, nsvc).groups()
    except (AttributeError, IndexError):
        raise gluetool.GlueError("'{}' is not a valid module nsvc".format(nsvc))


def nsvc_from_nvr(nvr):
    """
    Helper function to return a tuple of NSVC from an Brew/Koji compatible module NVR.

    :param: str nvr: NVR string.
    :rtype: tuple
    :returns: Tuple of N, S, V, C.
    :raises: gluetool.GlueError if NVR not valid.
    """

    try:
        (name, stream, version, context) = re.match(NVR_REGEX, nvr).groups()
        # underscore in stream number must be converted to '-'
        stream = stream.replace('_', '-')
    except (AttributeError, IndexError):
        raise gluetool.GlueError("'{}' is not a valid module nvr".format(nvr))

    return (name, stream, version, context)


class MBSApi(object):

    def __init__(self, mbs_api_url, mbs_ui_url, module):
        self.mbs_api_url = mbs_api_url
        self.mbs_ui_url = mbs_ui_url
        self.module = module

    @cached_property
    def about(self):
        """
        Returns MBS about endpoint as a namedtuple.

        :rtype: MBSAbout
        :returns: MBS about namedtuple with fields api_version, auth_method and version.
        """
        return MBSAbout(**self._get_json('module-build-service/1/about'))

    def _get_json(self, location, params=None):
        """
        Query MBS API endpoint location and return the JSON reply.

        :param str location: API endpoint to query.
        :param dict params: Query parameters
        :rtype: dict
        :returns: JSON output as a dictionary.
        """
        params = params or {}

        url = '{}/{}'.format(self.mbs_api_url, location)

        if params:
            # keep params sorted in the URL - makes testing possible
            sorted_params = collections.OrderedDict([
                (name, params[name]) for name in sorted(params.iterkeys())
            ])

            url = '{}?{}'.format(url, urllib.urlencode(sorted_params))

        self.module.debug('[MBS API]: {}'.format(url))

        with Action('query MBS API', parent=Action.current_action(), logger=self.module.logger, tags={
            'location': location,
            'params': params
        }):
            try:
                output = requests.get(url).json()
            except Exception:
                raise gluetool.GlueError('Unable to get: {}'.format(url))

        log_dict(self.module.debug, '[MBS API] output', output)

        return output

    def get_build_info_by_id(self, build_id, verbose=False):
        """
        Get MBS build information from build ID.

        :param int build_id: MBS build ID.
        :param boolean verbose: Verbose query.
        :rtype: dict
        :returns: JSON output with given build informations.
        """
        params = {'verbose': 1 if verbose else 0}

        return self._get_json('module-build-service/1/module-builds/{}'.format(build_id), params=params)

    def get_build_info_by_nsvc(self, nsvc_tuple, verbose=False):
        """
        Get MBS build information from NSVC tuple.

        :param tuple nsvc_tuple: Build NSVC as a tuple.
        :param boolean verbose: Verbose query.
        :rtype: dict
        :returns: JSON output with given build informations.
        """

        (name, stream, version, context) = nsvc_tuple

        url = 'module-build-service/1/module-builds/'
        params = {
            'name': name,
            'stream': stream,
            'version': version,
            'context': context,
            'verbose': 1 if verbose else 0
        }

        try:
            return self._get_json(url, params=params)['items'][0]
        except (IndexError, KeyError):
            # pylint: disable=line-too-long
            raise gluetool.GlueError("Could not find module with nsvc '{}:{}:{}:{}'".format(name, stream, version, context))  # Ignore PEP8Bear

    def get_build_ui_url(self, build_id):
        """
        Returns URL to the MBS web interface for the given build ID.

        :param int build_id: MBS build ID.
        :rtype: str
        :returns: URL to web interface of the MBS build.
        """
        return '{}/module/{}'.format(self.mbs_ui_url, build_id)


class MBSTask(object):
    # pylint: disable=too-few-public-methods,too-many-instance-attributes

    ARTIFACT_NAMESPACE = 'redhat-module'

    def __init__(self, module, build_id=None, nsvc=None, nvr=None):
        # pylint: disable=invalid-name

        self.module = module
        self.logger = module.logger
        module.logger.connect(self)

        mbs_api = module.mbs_api()

        if sum([bool(param) for param in [build_id, nsvc, nvr]]) != 1:
            raise gluetool.GlueError('module must be initialized only from one of build_id, nsvc or nvr')

        if build_id:
            build_info = mbs_api.get_build_info_by_id(build_id, verbose=True)

        if nsvc:
            build_info = mbs_api.get_build_info_by_nsvc(nsvc_from_string(nsvc), verbose=True)

        if nvr:
            build_info = mbs_api.get_build_info_by_nsvc(nsvc_from_nvr(nvr), verbose=True)

        self._build_info = build_info

        self.id = self.dispatch_id = build_info['id']
        self.name = build_info['name']
        self.component = self.name
        self.stream = build_info['stream']
        self.version = build_info['version']
        self.context = build_info['context']
        self.issuer = build_info['owner']
        self.scratch = build_info['scratch']
        self.nsvc = '{}:{}:{}:{}'.format(self.name, self.stream, self.version, self.context)

        # `nvr` is:
        # - often used as unique id of artifact (e.g. in mail notifications)
        # - same as nvr of module in Brew/Koji
        # - for modules the nvr is diffrent from NSVC, as it is delimited with '-' instead of ':'
        #   and also in case of stream the character '-' is replaced with '_', see:
        #   https://github.com/release-engineering/resultsdb-updater/pull/73#discussion_r235964781
        # - if build is scratch, the '+' and id is added to the end
        self.nvr = '{}-{}-{}.{}'.format(self.name, self.stream.replace('-', '_'), self.version, self.context)
        if self.scratch:
            self.nvr = '{}+{}'.format(self.nvr, self.id)

        # build tags from brew
        self.tags = [tag['name'] for tag in self.module.shared('koji_session').listTags(self.nvr)]

        # this string identifies component in static config file
        self.component_id = '{}:{}'.format(self.name, self.stream)

        # the target for modules uses platform stream, which nicely reflects the fact for which
        # release the module is built for, similarly to what build target in Brew/Koji does
        self.target = self.platform_stream

    @cached_property
    def platform_stream(self):
        """
        :rtype: str
        :returns: Platform stream from the modulemd document.
        """

        query = ".data.xmd.mbs.buildrequires.platform.stream"

        platform_stream = jq(query).transform(self._modulemd)

        if not platform_stream:
            raise gluetool.GlueError('Could not detect platform stream in modulemd document')

        return platform_stream.encode('ascii')

    @cached_property
    def _modulemd(self):
        """
        Returns ``modulemd`` document if available in build info. Describes details of the artifacts
        used to build the module. It is embedded in a form of string, containing the YAML document.
        This function extracts the string and unpacks its YAML-ness into a data structure it represents.

        :returns: ``modulemd`` structure of ``None`` if there's no ``modulemd`` key in the build info.
        """

        if 'modulemd' not in self._build_info:
            raise gluetool.GlueError('Artifact build info does not include modulemd document')

        modulemd = gluetool.utils.from_yaml(self._build_info['modulemd'])

        log_dict(self.debug, 'modulemd', modulemd)

        return modulemd

    @cached_property
    def has_artifacts(self):
        # pylint: disable=no-self-use

        # We believe MBS - and Brew behind it keeps artifacts "forever" - or, at least, long enough to matter to us
        # - therefore we don't even bother to check for their presence.

        return True

    @cached_property
    def task_arches(self):
        """
        :rtype: TaskArches
        :returns: Information about arches the task was building for
        """

        query = """
              .data.components.rpms
            | .[]
            | .arches
            | .[]
        """

        all_arches = jq(query).transform(self._modulemd, multiple_output=True)

        log_dict(self.debug, 'gathered module arches', all_arches)

        # Apparently, output from jq is unicode string, despite feeding it ascii-encoded. Encode each arch
        # string to ascii before while we're getting rid of duplicates.
        #
        # ``set`` to filter out duplicities, ``list`` to convert the set back to a list of uniq arches,
        # and ``sorted`` to make it easier to grab & read & test.
        arches = sorted(list(set([arch.encode('ascii') for arch in all_arches])))

        log_dict(self.debug, 'unique module arches', arches)

        return TaskArches(arches)

    @cached_property
    def dependencies(self):
        dependencies = []

        try:
            requires = self._modulemd['data']['dependencies'][0]['requires']
        except (AttributeError, KeyError) as error:
            raise gluetool.GlueError('Could not detect module dependecies: {}'.format(error))

        for module_name, module_streams in requires.iteritems():
            for stream in module_streams:
                dependencies.append('{}:{}'.format(module_name, stream))

        return dependencies

    @cached_property
    def url(self):
        return self.module.mbs_api().get_build_ui_url(self.id)

    @cached_property
    def distgit_ref(self):
        """
        Distgit ref id from which package has been built or ``None`` if it's impossible to find it.

        :rtype: str
        :returns: Dist-git ref of the build source.
        """
        try:
            return self._build_info['scmurl'].split('#')[1].encode('ascii')
        except IndexError:
            self.debug('Distgit ref not found in scmurl: {}'.format(self._build_info['scmurl']))
        return None


class MBS(gluetool.Module):
    name = 'mbs'
    description = 'Provides information about MBS (Module Build Service) artifact'

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    options = [
        ('MBS options', {
            'mbs-ui-url': {
                'help': 'URL of mbs ui server.',
                'type': str
            },
            'mbs-api-url': {
                'help': 'URL of mbs api server.',
                'type': str
            }
        }),
        ('Build initialization options', {
            'build-id': {
                'help': 'Initialize build from MBS build ID (default: none).',
                'action': 'append',
                'default': [],
            },
            'nsvc': {
                'help': 'Initialize build from NSVC (default: none).',
                'action': 'append',
                'default': [],
            },
            'nvr': {
                'help': 'Initialize build from NVR (default: none).',
                'action': 'append',
                'default': [],
            },
        })
    ]

    required_options = ('mbs-api-url',)

    shared_functions = ['primary_task', 'tasks', 'mbs_api']

    def __init__(self, *args, **kwargs):
        super(MBS, self).__init__(*args, **kwargs)
        self._tasks = []

    def primary_task(self):
        """
        Returns a `primary` module build, the first build in the list of current nodules.

        :rtype: :py:class:`MbsTask` or None
        :returns: Instance of an object represeting a module buil or None, if no modules are avaiable.
        """

        log_dict(self.debug, 'primary task - current modules', self._tasks)

        return self._tasks[0] if self._tasks else None

    def _init_mbs_builds(self, build_ids=None, nsvcs=None, nvrs=None):
        """
        Initializes MBS builds in parallel.

        :param list build_ids: List of module build IDs.
        :param list nsvcs: List of module NSVCs.
        :param list nvrs: List of NVRs of a module (compatible with brew/koji).

        :retype: list(MBSTask)
        :returns: List of initialized MBS builds.
        """
        build_ids = build_ids or []
        nsvcs = nsvcs or []
        nvrs = nvrs or []

        current_action = Action.current_action()

        # Our API routines call `Action.current_action` to get parent for their own actions,
        # and since we're spawning threads for our `MBSTask` calls, we need to provide
        # the initial action in each of those threads.
        def _init_trampoline(**kwargs):
            Action.set_thread_root(current_action)

            return MBSTask(self, **kwargs)

        with ThreadPoolExecutor(thread_name_prefix="api_thread") as executor:
            # initialized from build IDs
            futures = {
                executor.submit(_init_trampoline, build_id=build_id)
                for build_id in build_ids
            }

            # initialized from NSVCs
            futures.update({
                executor.submit(_init_trampoline, nsvc=nsvc)
                for nsvc in nsvcs
            })

            # initialized from NVRs
            futures.update({
                executor.submit(_init_trampoline, nvr=nvr)
                for nvr in nvrs
            })

            for future in wait(futures).done:
                self._tasks.append(future.result())

    def tasks(self, build_ids=None, nsvcs=None, nvrs=None):
        # type: (list, list, list) -> MBSTask
        """
        Returns list of module builds available. If any of the additional parameters
        are provided, modules list is extended with them first.

        :param list build_ids: List of module build IDs.
        :param list nsvcs: List of module NSVCs.
        :param list nvrs: List of NVRs of a module (compatible with brew/koji).

        :rtype: list(MBSTask)
        :returns: List of module builds.
        """
        if any([build_ids, nsvcs, nvrs]):
            self._init_mbs_builds(build_ids=build_ids, nsvcs=nsvcs, nvrs=nvrs)

        return self._tasks

    @property
    def eval_context(self):
        # pylint: disable=unused-variable
        __content__ = {  # noqa
            'ARTIFACT_TYPE': """
                             Type of the artifact, ``mbs-build`` in the case of ``mbs`` module.
                             """,
            'BUILD_TARGET': """
                            Build target for modules is the platform module stream name (e.g. el8, el8.1.0, etc).
                            """,
            'PRIMARY_TASK': """
                            Primary task, represented as ``MBSTask`` instance.
                            """,
            'TAGS': """
                    Module Brew/Koji build tags.
                    """,
            'TASKS': """
                     List of all tasks known to this module instance.
                     """
        }

        primary_task = self.primary_task()

        if not primary_task:
            self.debug('No primary task available, cannot pass it to eval_context')
            return {}

        return {
            # common for all artifact providers
            'ARTIFACT_TYPE': primary_task.ARTIFACT_NAMESPACE,
            'BUILD_TARGET': primary_task.target,
            'PRIMARY_TASK': primary_task,
            'TAGS': primary_task.tags,
            'TASKS': self.tasks()
        }

    @cached_property
    def _mbs_api(self):
        return MBSApi(self.option('mbs-api-url'), self.option('mbs-ui-url'), self)

    def mbs_api(self):
        # type: () -> MBSApi
        """
        Returns MBSApi instance.
        """
        return self._mbs_api

    def execute(self):
        # pylint: disable=line-too-long
        self.info("connected to MBS instance '{}' version '{}'".format(self.option('mbs-api-url'), self.mbs_api().about.version))  # Ignore PEP8Bear

        # koji/brew is required to get module tags
        self.require_shared('koji_session')

        if any([self.option(opt) for opt in ['build-id', 'nsvc', 'nvr']]):
            self._init_mbs_builds(
                build_ids=normalize_multistring_option(self.option('build-id')),
                nsvcs=normalize_multistring_option(self.option('nsvc')),
                nvrs=normalize_multistring_option(self.option('nvr'))
            )

        for task in self._tasks:
            self.info('Initialized with {}: {} ({})'.format(task.id, task.nsvc, task.url))
