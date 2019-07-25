import base64
import ConfigParser
import json
import os
import urllib
import urllib2
import sys

import jq
from jenkinsapi.jenkins import Jenkins
from requests.exceptions import RequestException

import gluetool
from gluetool import GlueError
from gluetool.log import format_dict, log_dict
from gluetool.proxy import Proxy
from gluetool.result import Result
from gluetool.utils import wait


DEFAULT_JENKINSAPI_TIMEOUT = 120
DEFAULT_JENKINSAPI_TIMEOUT_TICK = 30


class JenkinsProxy(Proxy):
    # pylint: disable=too-few-public-methods

    """
    Proxy wrapper of a Jenkins API instance. Instance of this class
    behaves exactly like the Jenkins API instance it wraps, user of such
    instance can use it like any other Jenkins API instance.

    To us such arrangement brings possibility to extend proxy object with
    our custom methods. That way we can provide a single object (via our
    shared function) that combines both the original behavior of Jenkins
    API instance, and our custom function we want to provide to the users
    of this module.

    When adding new methods, don't forget to update _CUSTOM_METHODS as well.

    :param CIJenkins module: our parent module.
    :param jenkinsapi.jenkins jenkins: Jenkins API connection.
    """

    _CUSTOM_METHODS = ('set_build_name', 'enable_quiet_mode', 'disable_quiet_mode', 'invoke_job')

    def __init__(self, jenkins, module):
        super(JenkinsProxy, self).__init__(jenkins)

        # This is a proxy, so 'self.foo' would change attribute of
        # the wrapped object. We don't want to mess with its attributes,
        # so we have to resort to using object's methods when we want
        # to change *this* object instead.
        object.__setattr__(self, '_module', module)

    def __getattribute__(self, name):
        """
        Original __getattribute__ method of Proxy class just forwards all
        its calls to the object Proxy wraps. To allow users use of our custom
        methods, we must "teach" our __getattribute__ about our methods.
        """

        if name in JenkinsProxy._CUSTOM_METHODS:
            return object.__getattribute__(self, name)

        return super(JenkinsProxy, self).__getattribute__(name)

    def set_build_name(self, name, description=None, build_url=None):
        """
        Set name (and possibly description) of a jenkins build.

        :param str name: desired name.
        :param str description: if not set, empty string is used.
        :param str build_url: URL of a jenkins build. If not set, method tries to find
          it using $BUILD_URL env var.
        """

        module = object.__getattribute__(self, '_module')

        if build_url is None:
            build_url = os.getenv('BUILD_URL', None)

            if build_url is None:
                raise GlueError('$BUILD_URL env var not found, was this job started by Jenkins?')

        description = description or ''

        module.jenkins_rest(build_url + '/configSubmit', **{
            'displayName': name,
            'description': description
        })

        module.debug("build name set:\n  name='{}'\n  description='{}'".format(
            name, description))

    def enable_quiet_mode(self):
        """
        Enable "quiet" mode - Jenkins will accept triggers and queue builds but it won't start them
        on slaves.
        """

        module = object.__getattribute__(self, '_module')

        return module.jenkins_rest('{}/quietDown'.format(module.option('url')))

    def disable_quiet_mode(self):
        """
        Disable "quiet" mode - Jenkins will start queued builds.
        """

        module = object.__getattribute__(self, '_module')

        return module.jenkins_rest('{}/cancelQuietDown'.format(module.option('url')))

    def invoke_job(self, job_name, build_params):
        # type: (str, Dict[str, Any]) -> Any
        """
        Invoke the Jenkins build.

        :param str job_name: name of the Jenkins job to invoke.
        :param dict build_params: build parameters.
        """

        module = object.__getattribute__(self, '_module')

        log_dict(module.debug, "invoking job '{}'".format(job_name), build_params)

        if not module.dryrun_allows('Invoking a job'):
            return None

        queue_item = self[job_name].invoke(build_params=build_params)

        module.info("invoked job '{}' with given parameters".format(job_name))

        return queue_item


class CIJenkins(gluetool.Module):
    """
    This modules provides connection to a jenkins instance via jenkinsapi module:
        https://jenkinsapi.readthedocs.io/en/latest/

    You can use the option '--create-jjb-config' to force creation of JJB config file.

    This module is dry-run safe as long as its users stick functionality added by this module
    on top of Jenkins API provided by the ``jenkinsapi`` library this module wraps.
    Direct use of its functionality is still allowed and it is not controlled with respect to the dry-run mode.
    """

    name = 'jenkins'
    description = 'Connect to a jenkins instance via jenkinsapi'
    requires = 'jenkinsapi'
    # dry mod not fully, it can be bypassed by self.shared('jenkins')['foo'].invoke(...)
    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    # shared jenkins object
    _jenkins = None

    options = {
        'create-jjb-config': {
            'help': 'Force creation Jenkins Job Builder configuration (default: %(default)s).',
            'default': None,
            'metavar': 'FILE',
            'type': str
        },
        'password': {
            'help': 'Jenkins admin password (default: %(default)s)',
            'default': None
        },
        'url': {
            'help': 'Jenkins URL (e.g. http://localhost:8080)',
        },
        'username': {
            'help': 'Jenkins admin username (default: %(default)s)',
            'default': None
        },
        'no-ssl-verify': {
            'help': 'Do not verify HTTPS certificate (default: %(default)s).',
            'action': 'store_true',
            'default': 'no'
        },
        'jenkins-api-timeout': {
            'help': 'Wait SECONDS for Jenkins to respond (default: %(default)s).',
            'type': int,
            'default': DEFAULT_JENKINSAPI_TIMEOUT,
            'metavar': 'SECONDS'
        },
        'jenkins-api-timeout-tick': {
            'help': 'Try every SECONDS to send a request to Jenkins (default: %(default)s).',
            'type': int,
            'default': DEFAULT_JENKINSAPI_TIMEOUT_TICK,
            'metavar': 'SECONDS'
        }
    }
    required_options = ['url']
    shared_functions = ['jenkins', 'jenkins_rest', 'get_jenkins_build']

    def jenkins(self, reconnect=False):
        """ return jenkinsapi.Jenkins object instance """
        if reconnect:
            self.connect()

        return self._jenkins

    def jenkins_rest(self, url, wait_timeout=None, wait_tick=None, **data):
        """
        Submit request to Jenkins via its http interface.

        :param str url: URL to send request to. Can be absolute, e.g. when
          caller gets its base from BUILD_URL env var, or relative, starting
          with '/'. Configured Jenkjins URL is prepended to relative URLS,
          while absolute URLs must lead to this configured Jenkins instance.
        :param dict data: data to submit to the URL.
        :param int wait_timeout: number of seconds to wait for Jenkins successful response.
        :returns: (response, resonse-content)
        """

        self.debug("Jenkins REST request: url='{}'\n{}".format(url, format_dict(data)))

        if url.startswith('/'):
            url = self.option('url') + url

        elif not url.startswith(self.option('url')):
            raise GlueError('Cross-site Jenkins REST request')

        if data:
            data = urllib.urlencode({
                'json': json.dumps(data)
            })

        username, password = self.option('username'), self.option('password')

        if not self.dryrun_allows('Submit REST request to jenkins'):
            return None, None

        if username or password:
            request = urllib2.Request(url)
            base64string = base64.b64encode('{}:{}'.format(username, password))
            request.add_header('Authorization', 'Basic {}'.format(base64string))
            response = urllib2.urlopen(request, data)

        else:
            request = url

        def _request():
            response = urllib2.urlopen(request, data)
            return Result.Ok(response) if response.getcode() == 200 else Result.Error(response)

        timeout = wait_timeout or self.option('jenkins-api-timeout')
        tick = wait_tick or self.option('jenkins-api-timeout-tick')

        response = wait('waiting for Jenkins to respond successfully', _request, timeout=timeout, tick=tick)

        code, content = response.getcode(), response.read()
        gluetool.log.log_blob(self.debug, 'response: {}'.format(code), content)

        return response, content

    def get_jenkins_build(self, job_name=None, build_id=None):
        """
        Return (arbitrary) Jenkins build representation.

        Without any options, returns the current Jenkins build.

        :param str job_name: job whose build we're looking for. If not set, ``JOB_NAME`` env variable is used.
        :param build_id: build ID we're looking for. If not set, ``BUILD_ID`` env variable is used.
        """

        if job_name is None:
            job_name = os.getenv('JOB_NAME', None)

        if build_id is None:
            build_id = os.getenv('BUILD_ID', None)

        self.debug('looking for Jenkins build of {}:{}'.format(job_name, build_id))

        if not job_name or not build_id:
            raise GlueError("Cannot search for the Jenkins build for '{}:{}'".format(job_name, build_id))

        job = self.jenkins(reconnect=True)[job_name]

        return job.get_build(int(build_id))

    def create_jjb_config(self):
        password = self.option('password')
        url = self.option('url')
        user = self.option('username')

        config_file = gluetool.utils.normalize_path(self.option('create-jjb-config'))
        config_dir = os.path.dirname(config_file)

        # create configuration
        config = ConfigParser.RawConfigParser()
        config.add_section('jenkins')
        config.set('jenkins', 'url', url)
        if user:
            config.set('jenkins', 'user', user)
        if password:
            config.set('jenkins', 'password', password)

        # make sure directory structure exists
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)

        # save the configuration
        with open(config_file, 'wb') as f:
            config.write(f)

        self.info("created jjb configuration in '{}'".format(config_file))

    @gluetool.utils.cached_property
    def _jenkins_build_params(self):
        """
        Parameters used when the Jenkins build, running this pipeline, was triggered, or ``None`` if we
        cannot acquire the data.

        Params are not going to change, therefore the code is hidden behind a cached property to avoid
        pointless API calls.
        """

        # In theory, everything is perfectly valid. In real life, the production Jenkins stuggles to even provide
        # a list of builds for the job, and often respond with Proxy Timeouts. Extending timeouts in such conditions
        # makes no sense, it'd simply timeout later - and we're dealing with a reverse proxy, the actual Jenkins
        # behind it still works on a pointless request whose output wouldn't be seen by anybody.
        #
        # But: we can send request directly to the actual endpoint, without traversing its bunch of other structures
        # (that's making the API way so slow).

        try:
            jenkins_build = self.get_jenkins_build()

            if not jenkins_build:
                return None

            with gluetool.utils.requests() as req:
                response = req.get('{}/job/{}/{}/api/json?pretty=true'.format(
                    self.option('url'),
                    jenkins_build.job.name,
                    jenkins_build.buildno
                ))

            if response.status_code != 200:
                # Might be nice to track this condition, alhtough we can manage without the params, hence
                # not an exception.
                self.warn('Cannot fetch job info, server responsed with {}'.format(response.status_code), sentry=True)

                return None

            build_info = response.json()

            log_dict(self.debug, 'raw build info', build_info)

            query = """
                  .actions
                | .[]
                | select( ._class == "hudson.model.ParametersAction" )
                | .parameters
                | .[]
                | {"name": .name, "value": .value}
            """

            extracted_params = jq.jq(query).transform(build_info, multiple_output=True)

            log_dict(self.debug, 'extracted build params', extracted_params)

            # We have a list of dicts (name: value), that can be reduced to a single dict with all the params.
            params = {
                param['name']: param['value'] for param in extracted_params
            }

            log_dict(self.debug, 'build params', params)

            return params

        # pylint: disable=broad-except
        except Exception:
            self.glue.sentry_submit_exception(gluetool.Failure(self, sys.exc_info()), logger=self.logger)

            self.error('Failed to download the Jenkins build for parameters')

        return None

    @property
    def eval_context(self):
        # pylint: disable=unused-variable
        __content__ = {  # noqa
            'JENKINS_URL': """
                           URL of the Jenkins server running this module. If it cannot be determined,
                           the value is ``None``. ``JENKINS_URL`` environment variable is the primary
                           source of this information.
                           """,
            'JENKINS_BUILD_ID': """
                                ID ("number") of the Jenkins build running this module, within its
                                parent job. If it cannot be determined, the value is ``None``. ``BUILD_ID``
                                environment variable is the primary source of this value.
                                """,
            'JENKINS_BUILD_URL': """
                                 URL of the Jenkins build running this module. If it cannot be determined,
                                 the value is ``None``. ``BUILD_URL`` environment variable is the primary source
                                 of this value.
                                 """,
            'JENKINS_JOB_NAME': """
                                Name of the Jenkins job the build running this module belongs to. If it
                                cannot be determined, the value is ``None``. ``JOB_NAME`` environment variable
                                is the primary source of this information.
                                """,
            'JENKINS_JOB_URL': """
                               URL of the Jenkins job the build running this module belongs to. If it
                               cannot be determined, the value is ``None``. ``JOB_URL`` environment variable
                               is the primary source of this information.
                               """,
            'JENKINS_BUILD_PARAMS': """
                                    Dictionary with parameters the Jenkins build was triggered with. If there's
                                    no Jenkins build reachable - not ``JOB_NAME`` nor ``BUILD_ID`` environment
                                    variables - then the value is ``None``.
                                    """
        }

        return {
            'JENKINS_URL': os.getenv('JENKINS_URL', None),
            'JENKINS_BUILD_ID': os.getenv('BUILD_ID', None),
            'JENKINS_BUILD_URL': os.getenv('BUILD_URL', None),
            'JENKINS_JOB_NAME': os.getenv('JOB_NAME', None),
            'JENKINS_JOB_URL': os.getenv('JOB_URL', None),
            'JENKINS_BUILD_PARAMS': self._jenkins_build_params
        }

    def connect(self):
        password = self.option('password')
        url = self.option('url')
        user = self.option('username')
        ssl_verify = not gluetool.utils.normalize_bool_option(self.option('no-ssl-verify'))

        # connect to the jenkins instance
        try:
            jenkins = Jenkins(url, username=user, password=password,
                              ssl_verify=ssl_verify,
                              timeout=self.option('jenkins-api-timeout'))

        except RequestException as e:
            self.debug('Connection error: {}'.format(e))
            raise gluetool.GlueError("could not connect to jenkins '{}': {}".format(url, str(e)))

        self._jenkins = JenkinsProxy(jenkins, self)

    def execute(self):
        url = self.option('url')

        # create JJB configuration file if forced
        if self.option('create-jjb-config'):
            self.create_jjb_config()

        # check if dry-run level is enabled, warn user
        if self.dryrun_enabled:
            self.warn(
                "DRY mode supported for functionality provided by this module, without direct use of jenkins api")
        # connecto to jenkins
        self.connect()

        # be informative about the jenkins connection
        self.info('connected to jenkins \'{}\' version {}'.format(url, self._jenkins.version))
