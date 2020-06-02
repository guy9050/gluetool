import enum
import os
import re
import tempfile

from six.moves.configparser import ConfigParser

import gluetool
from gluetool.utils import Command

from gluetool_modules.libs.results import TestResult, publish_result

# Type annotations
from typing import Any, Iterator, List, NoReturn, TYPE_CHECKING  # noqa

if TYPE_CHECKING:
    from gluetool_modules.infrastructure import github  # noqa


COPR_CONFIG = 'copr.conf'
STATUS_CONTEXT = 'continuous-integration/tft-ci/copr'


class ValuesEnum(enum.Enum):
    @classmethod
    def values(cls):
        # type: () -> List[Any]
        """
        Return list of values associated with the enum.
        """
        return [e.value for e in cls]


class CoprMethod(ValuesEnum):
    """
    Enumeration of supported methods to trigger copr build.
    """
    MAKEFILE_METHOD = 'makefile'
    """Schedule a copr build by invoking `make copr_build` command."""


class Artifact(ValuesEnum):
    """
    Enumeration of supported artifact types.
    """
    GITHUB_ARTIFACT = 'github-pr'
    """Schedule a copr build on GitHub pull request artifact."""


def _assert_never(x):
    # type: (Any) -> NoReturn
    assert False, "Unhandled {}: {}".format(type(x).__name__, x)


class CoprBuildBuildTestResult(TestResult):
    def __init__(self, glue, overall_result, build_url, process_output, **kwargs):
        # type: (gluetool.Glue, str, str, gluetool.utils.ProcessOutput, **Any) -> None
        super(CoprBuildBuildTestResult, self).__init__(glue, 'copr-build', overall_result, **kwargs)

        self.build_url = build_url
        self.process_output = process_output


class CoprBuildFailedError(gluetool.SoftGlueError):
    def __init__(self, message, output):
        # type: (str, gluetool.utils.ProcessOutput) -> None
        super(CoprBuildFailedError, self).__init__(message)
        self.output = output


class CoprBuilder(gluetool.Module):
    """
    This module schedules a copr build from provided artifact.

    Currently the only supported artifact is `github-pr` from the `github` module,
    and the only build method is `makefile` by invoking `make copr_build` command.
    """

    name = 'copr-builder'
    description = 'Triggers new copr build'

    options = [
        ('General options', {
            'method': {
                'help': 'Method for triggering the copr build (default: %(default)s).',
                'choices': CoprMethod.values(),
                'default': CoprMethod.MAKEFILE_METHOD
            },
            'status-context': {
                'help': 'Context for updating status in case GitHub pull request is used (default: %(default)s).',
                'default': STATUS_CONTEXT
            },
        }),
        ('Copr options', {
            'copr-url': {
                'help': 'Copr url.',
            },
            'copr-login': {
                'help': 'Copr login.',
            },
            'copr-username': {
                'help': 'Copr username.',
            },
            'copr-token': {
                'help': 'Copr token.',
            },
        }),
    ]

    required_options = ('method', 'copr-url', 'copr-login', 'copr-username', 'copr-token')

    def __init__(self, *args, **kwargs):
        # type: (*Any, **Any) -> None
        super(CoprBuilder, self).__init__(*args, **kwargs)
        self.workdir = tempfile.mkdtemp(prefix=CoprBuilder.name, dir=os.getcwd())
        self.info('Created working directory {}.'.format(self.workdir))

    def _log_and_raise(self, message, blob):
        # type: (str, Any) -> None
        """
        Log error and raise exception.
        """

        gluetool.log.log_blob(self.error, message, blob)
        raise gluetool.GlueError('{}, cannot continue'.format(message))

    def _run_copr_build(self):
        # type: () -> str
        """
        Trigger copr build and return url of the copr build.
        """

        self.require_shared('primary_task')
        artifact = self.shared('primary_task')

        try:
            artifact_type = Artifact(artifact.ARTIFACT_NAMESPACE)
        except ValueError:
            raise gluetool.GlueError('Unsupported artifact: {}, supported is only {}'.format(
                artifact.ARTIFACT_NAMESPACE, Artifact.values()))

        if artifact_type is Artifact.GITHUB_ARTIFACT:
            self.require_shared('set_pr_status')
            return self._copr_build_from_github(artifact)

        _assert_never(artifact_type)

    def _copr_build_from_github(self, pull_request):
        # type: (github.GitHubPullRequest) -> str
        """
        Trigger copr build on source fetched from GitHub and return url of the copr build.
        """

        self._fetch_from_github(pull_request)

        try:
            method = CoprMethod(self.option('method'))
        except ValueError:
            raise gluetool.GlueError('Unsupported method to trigger copr build: {}, supported is only {}'.format(
                self.option('method'), CoprMethod.values()))

        if method is CoprMethod.MAKEFILE_METHOD:
            return self._run_make_copr(pull_request)

        _assert_never(method)

    def _fetch_from_github(self, pull_request):
        # type: (github.GitHubPullRequest) -> None
        """
        Fetch source from GitHub.
        """

        clone_url = pull_request.clone_url
        pull_number = pull_request.pull_number
        commit_sha = pull_request.commit_sha
        source_branch = pull_request.source_branch
        repo_name = pull_request.repo

        clone_cmd = ['git',  'clone', clone_url, repo_name]
        try:
            Command(clone_cmd).run(cwd=self.workdir)
        except gluetool.GlueCommandError as exc:
            self._log_and_raise('Failed to clone {}'.format(clone_url), exc.output.stderr)

        self.info('Successfully cloned {}.'.format(clone_url))

        fetch_cmd = ['git', 'fetch', 'origin', 'refs/pull/{}/head'.format(pull_number)]
        try:
            Command(fetch_cmd).run(cwd=os.path.join(self.workdir, repo_name))
        except gluetool.GlueCommandError as exc:
            self._log_and_raise('Failed to fetch pull request {}'.format(pull_number), exc.output.stderr)

        self.info('Successfully fetched pull request {}.'.format(pull_number))

        checkout_cmd = ['git', 'checkout', '-b', source_branch, commit_sha]
        try:
            Command(checkout_cmd).run(cwd=os.path.join(self.workdir, repo_name))
        except gluetool.GlueCommandError as exc:
            self._log_and_raise('Failed to checkout commit {}'.format(commit_sha), exc.output.stderr)

        self.info('Successfully checked out commit {}.'.format(commit_sha))

    def _run_make_copr(self, pull_request):
        # type: (github.GitHubPullRequest) -> str
        """
        Trigger copr build by invoking Makefile and return url of the copr build.
        """

        pull_number = pull_request.pull_number
        repo_name = pull_request.repo
        jenkins_build_url = self.shared('eval_context').get('JENKINS_BUILD_URL')

        self.shared('set_pr_status', 'pending', 'Copr build started.',
                    context=STATUS_CONTEXT, target_url=jenkins_build_url)

        self._create_copr_config(pull_request)
        copr_cmd = ['make', 'copr_build']
        copr_env = {'COPR_CONFIG': COPR_CONFIG, 'PR': pull_number}
        try:
            gluetool.log.log_dict(self.info, 'Starting copr build with environment variables', copr_env)
            env = dict(os.environ, **copr_env)
            output = Command(copr_cmd).run(inspect=True, env=env, cwd=os.path.join(self.workdir, repo_name))
        except gluetool.GlueCommandError as exc:
            self.shared('set_pr_status', 'failure', 'Copr build failed.',
                        context=STATUS_CONTEXT, target_url=jenkins_build_url)
            raise CoprBuildFailedError('Copr build failed.', exc.output)

        if not output.stdout:
            raise CoprBuildFailedError('No output from copr build.', output)

        matches = re.findall(r'^\s*https://.*$', output.stdout, re.M)
        if not matches:
            raise gluetool.GlueError('Unable to find copr build url.')
        copr_build_url = matches[-1].strip()  # type: str
        gluetool.log.log_blob(self.info, 'Build in copr was successful: {}'.format(copr_build_url), output.stdout)

        self.shared('set_pr_status', 'success', 'Copr build succeeded.',
                    context=STATUS_CONTEXT, target_url=copr_build_url)

        return copr_build_url

    def _create_copr_config(self, pull_request):
        # type: (github.GitHubPullRequest) -> None
        """
        Create copr config file.
        """

        copr_config = ConfigParser()

        copr_config.add_section('copr-cli')
        copr_config.set('copr-cli', 'copr_url', self.option('copr-url'))
        copr_config.set('copr-cli', 'login', self.option('copr-login'))
        copr_config.set('copr-cli', 'username', self.option('copr-username'))
        copr_config.set('copr-cli', 'token', self.option('copr-token'))

        with open(os.path.join(self.workdir, pull_request.repo, COPR_CONFIG), 'w') as config_file:
            config_file.write('# Copr config for {}\n'.format(pull_request.clone_url))
            copr_config.write(config_file)

    def execute(self):
        # type: () -> None
        try:
            copr_build_url = self._run_copr_build()
        except CoprBuildFailedError as exc:
            self.error('Result of testing: FAILED')
            publish_result(self, CoprBuildBuildTestResult, 'FAILED', None, exc.output)
            return

        self.info('Result of testing: PASSED')
        publish_result(self, CoprBuildBuildTestResult, 'PASSED', copr_build_url)
