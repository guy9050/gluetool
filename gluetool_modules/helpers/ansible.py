import os
import re

import gluetool
from gluetool.action import Action
from gluetool.utils import Command, from_json
from gluetool.log import format_blob, log_blob, log_dict
from libci.sentry import PrimaryTaskFingerprintsMixin

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import cast, TYPE_CHECKING, Any, Dict, List, NamedTuple, Optional, Tuple, Union  # noqa

if TYPE_CHECKING:
    import libci.guest  # noqa


# possible python interpreters
ANSIBLE_PYTHON_INTERPRETERS = ["/usr/bin/python3", "/usr/bin/python2", "/usr/libexec/platform-python"]


# Default name of log file with Ansible output
ANSIBLE_OUTPUT = "ansible-output.txt"


#: Represents bundle of information we know about Ansible output.
#:
#: :ivar gluetool.utils.ProcessOutput execution_output: raw output of the command
#:     as returned by :py:meth:`gluetool.utils.Command.run`.
#: :ivar str json_output: if set, a Python data structure representing Ansible output.
#: :ivar str log_filepath: local path to a file with Ansible output.
AnsibleOutput = NamedTuple('RestraintOutput', (
    ('execution_output', gluetool.utils.ProcessOutput),
    ('json_output', Optional[Any]),
    ('log_filepath', str)
))


class PlaybookError(PrimaryTaskFingerprintsMixin, gluetool.GlueError):
    def __init__(self, task, ansible_output):
        # type: (Any, gluetool.utils.ProcessOutput) -> None

        super(PlaybookError, self).__init__(task, 'Failure during Ansible playbook execution')

        self.ansible_output = ansible_output


class Ansible(gluetool.Module):
    """
    Helper module - give it a playbook, a guest, maybe few additional variables,
    and let Ansible perform it.

    Usually, guests are provided by other provisioning modules, e.g. ``openstack``
    or ``docker-provisioner``, playbooks are up to you.
    """

    name = 'ansible'
    description = 'Run an Ansible playbook on a given guest.'

    options = {
        'ansible-playbook-options': {
            'help': "Additional ansible-playbook options, for example '-vvv'. (default: none)",
            'action': 'append',
            'default': []
        }
    }

    shared_functions = ['run_playbook', 'detect_ansible_interpreter']

    supported_dryrun_level = gluetool.glue.DryRunLevels.DRY

    @gluetool.utils.cached_property
    def additional_options(self):
        # type: () -> List[str]

        return gluetool.utils.normalize_multistring_option(self.option('ansible-playbook-options'))

    def detect_ansible_interpreter(self, guest):
        # type: (libci.guest.NetworkedGuest) -> List[str]
        """
        Detect Ansible's python interpreter on the given guest and return it.

        :param libci.guest.NetworkedGuest guest: Guest for auto-detection
        :returns: List of paths to the auto-detected python interpreters. Empty list if auto-detection failed.
        """

        assert guest.hostname is not None
        assert guest.key is not None

        cmd = [
            'ansible',
            '--inventory', '{},'.format(guest.hostname),
            '--private-key', guest.key,
            '--module-name', 'raw',
            '--args', 'command -v ' + ' '.join(ANSIBLE_PYTHON_INTERPRETERS),
            '--ssh-common-args',
            ' '.join(['-o ' + option for option in guest.options]),
            guest.hostname
        ]

        if guest.username:
            cmd += ['--user', guest.username]

        try:
            ansible_call = Command(cmd, logger=guest.logger).run()

        except gluetool.GlueCommandError as exc:
            self.warn('failed to auto-detect Ansible python interpreter\n{}'.format(
                exc.output.stdout))

            return []

        if not ansible_call.stdout:
            raise gluetool.GlueError('Ansible did not produce usable output')

        available_interpreters = [
            intrp for intrp in ansible_call.stdout.splitlines() if intrp in ANSIBLE_PYTHON_INTERPRETERS
        ]

        log_dict(guest.debug, 'available interpreters', available_interpreters)

        return available_interpreters

    # pylint: disable=too-many-arguments
    def run_playbook(self,
                     playbook_paths,  # type: Union[str, List[str]]
                     guest,  # type: libci.guest.NetworkedGuest
                     variables=None,  # type: Optional[Dict[str, Any]]
                     inventory=None,  # type: Optional[str]
                     cwd=None,  # type: Optional[str]
                     json_output=True,  # type: bool
                     log_filepath=None  # type: Optional[str]
                    ):  # noqa
        # type: (...) -> AnsibleOutput
        """
        Run Ansible playbook on a given guest.

        :param str or list playbook_paths: Path to the playbook or a list of playbook paths.
        :param libci.guest.NetworkedGuest guest: Guest to run playbooks on.
        :param dict variables: If set, represents additional variables that will
            be passed to ``ansible-playbook`` using ``--extra-vars`` option.
        :param str inventory: A path to the inventory file. You can use it if you
            want to cheat the ansible module e.g. to overshadow localhost with another host.
        :param str cwd: A path to a directory where ansible will be executed from.
        :param bool json_output: Ansible returns response as json if set.
        :param str log_filepath: Path to a file to store Ansible output in. If not set, ``ansible-output.txt``
            is created in the current directory.
        :returns: Instance of :py:class:`AnsibleOutput`.
        """

        if isinstance(playbook_paths, str):
            playbook_paths = [playbook_paths]

        log_dict(guest.debug, 'running playbooks', playbook_paths)

        assert guest.key is not None
        assert guest.environment is not None

        inventory = inventory or '{},'.format(guest.hostname)  # note the comma

        cmd = [
            'ansible-playbook',
            '-i', inventory,
            '--private-key', guest.key
        ]

        if guest.username:
            cmd += ['--user', guest.username]

        if variables:
            log_dict(guest.debug, 'variables', variables)

            cmd += [
                '--extra-vars',
                ' '.join(['{}="{}"'.format(k, v) for k, v in variables.iteritems()])
            ]

        cmd += self.additional_options

        if not self.dryrun_allows('Running a playbook in non-check mode'):
            guest.debug("dry run enabled, telling ansible to use 'check' mode")

            cmd += ['-C']

        cmd += [gluetool.utils.normalize_path(path) for path in playbook_paths]

        env_variables = os.environ.copy()

        if json_output:
            env_variables.update({'ANSIBLE_STDOUT_CALLBACK': 'json'})

        with Action(
            'running playbooks',
            parent=Action.current_action(),
            logger=guest.logger,
            tags={
                'guest': {
                    'hostname': guest.hostname,
                    'environment': guest.environment.serialize_to_json()
                },
                'playbook-paths': playbook_paths
            }
        ):
            try:
                ansible_call = Command(cmd, logger=guest.logger).run(cwd=cwd, env=env_variables)

            except gluetool.GlueCommandError as exc:
                ansible_call = exc.output

        # As path of logs, use the given file, with fallback to current directory & default name.
        if not log_filepath:
            log_filepath = os.path.join(os.getcwd(), ANSIBLE_OUTPUT)

        with open(log_filepath, 'w') as f:
            def _write(label, s):
                # type: (str, str) -> None

                f.write('{}\n{}\n\n'.format(label, s))

            _write('# STDOUT:', format_blob(cast(str, ansible_call.stdout)))
            _write('# STDERR:', format_blob(cast(str, ansible_call.stderr)))

            f.flush()

        def show_ansible_errors(output):
            # type: (gluetool.utils.ProcessOutput) -> None

            if output.stdout:
                log_blob(
                    guest.error,
                    'Last 30 lines of Ansible stdout', '\n'.join(output.stdout.splitlines()[-30:])
                )

            if output.stderr:
                log_blob(
                    guest.error,
                    'Last 30 lines of Ansible stderr', '\n'.join(output.stderr.splitlines()[-30:])
                )

        if json_output:
            # With `-v` option, ansible-playbook produces additional output, placed before the JSON
            # blob. Find the first '{' on a new line, that should be the start of the actual JSON data.
            if not ansible_call.stdout:
                show_ansible_errors(ansible_call)

                raise gluetool.GlueError('Ansible did not produce usable output')

            match = re.search(r'^{', ansible_call.stdout, flags=re.M)
            if not match:
                show_ansible_errors(ansible_call)

                raise gluetool.GlueError('Ansible did not produce JSON output')

            ansible_json_output = from_json(ansible_call.stdout[match.start():])

            log_dict(
                guest.debug,
                'Ansible json output', ansible_json_output
            )

        else:
            ansible_json_output = None

        if ansible_call.exit_code != 0:
            show_ansible_errors(ansible_call)

            primary_task = self.shared('primary_task')
            if primary_task:
                raise PlaybookError(primary_task, ansible_call)

            raise gluetool.GlueError('Failure during Ansible playbook execution')

        return AnsibleOutput(
            execution_output=ansible_call,
            json_output=ansible_json_output,
            log_filepath=log_filepath
        )
