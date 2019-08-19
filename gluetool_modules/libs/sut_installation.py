import collections
import os
import gluetool
from gluetool import SoftGlueError
from gluetool.log import log_dict, log_blob, LoggingFunctionType
from libci.sentry import PrimaryTaskFingerprintsMixin

# pylint: disable=no-name-in-module
from jq import jq

from .artifacts import artifacts_location

# Type annotations
# pylint: disable=unused-import,wrong-import-order,ungrouped-imports
from typing import TYPE_CHECKING, cast, Any, Dict, List, Tuple, Union, Optional, Callable  # noqa

if TYPE_CHECKING:
    import libci.guest # noqa

#: Step callback type
# pylint: disable=invalid-name
StepCallbackType = Callable[[str, gluetool.utils.ProcessOutput], None]

#: Describes one command used to SUT installtion
#:
#: :ivar str label: Label used for logging.
#: :ivar str command: Command to execute on the guest, executed once for each item from items.
#:                    It can contain a placeholder ({}) which is substituted by the current item.
#: :ivar list(str) items: Items to execute command with replaced to `command`.
#: :ivar bool ignore_exception: Indicates whether to raise `SUTInstallationFailedError` when command fails.
#: :ivar Callable callback: Callback to additional processing of command output.
SUTStep = collections.namedtuple('SUTStep', ['label', 'command', 'items', 'ignore_exception', 'callback'])


class SUTInstallationFailedError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    # pylint: disable=too-many-arguments
    def __init__(self, task, guest, items=None, reason=None, installation_logs=None, installation_logs_location=None):
        # type: (Any, libci.guest.Guest, Any, Optional[str], Optional[str], Optional[str]) -> None

        super(SUTInstallationFailedError, self).__init__(task, 'SUT installation failed')

        self.guest = guest
        self.items = items
        self.reason = reason
        self.installation_logs = installation_logs
        self.installation_logs_location = installation_logs_location


class SUTInstallation(object):

    def __init__(self, module, log_dirpath, primary_task, logger=None):
        # type: (gluetool.Module, str, Any, Optional[gluetool.log.ContextAdapter]) -> None

        self.module = module
        self.log_dirpath = log_dirpath
        self.primary_task = primary_task
        self.steps = []  # type: List[SUTStep]
        self.logger = logger or gluetool.log.Logging.get_logger()

    def add_step(self, label, command, items=None, ignore_exception=False, callback=None):
        # pylint: disable=too-many-arguments
        # type: (str, str, Union[Optional[str], Optional[List[str]]], bool, Optional[StepCallbackType]) -> None

        if not items:
            items = []

        if not isinstance(items, list):
            items = [items]

        self.steps.append(SUTStep(label, command, items, ignore_exception, callback))

    def run(self, guest):
        # type: (libci.guest.NetworkedGuest) -> None

        def _run_and_log(command, log_filepath, callback):
            # type: (str, str, Optional[Callable]) -> Tuple[bool, Optional[str]]
            # Set to `True` when the exception was raised by a command - we cannot immediately
            # raise `SUTInstallationFailedError` because we want to log output of the command,
            # and we cannot use `exc` and check whether it's not `None` because Python will
            # unset `exc` when leaving `except` branch.
            execute_failed = False
            error_message = None

            try:
                output = guest.execute(command)

            except gluetool.glue.GlueCommandError as exc:
                execute_failed = True
                output = exc.output

            if callback:
                error_message = callback(command, output)

            with open(log_filepath, 'a') as log_file:
                # pylint: disable=unused-argument
                def write_cover(text, **kwargs):
                    # type: (str) -> None
                    assert log_file is not None
                    log_file.write('{}\n\n'.format(text))

                log_blob(cast(LoggingFunctionType, write_cover), 'Command', command)
                log_blob(cast(LoggingFunctionType, write_cover), 'Stdout', output.stdout or '')
                log_blob(cast(LoggingFunctionType, write_cover), 'Stderr', output.stderr or '')

            return bool(execute_failed or error_message), error_message

        try:
            guest.execute('command -v yum')
            yum_present = True
        except gluetool.glue.GlueCommandError:
            yum_present = False

        if not os.path.exists(self.log_dirpath):
            os.mkdir(self.log_dirpath)

        logs_location = artifacts_location(self.module, self.log_dirpath, logger=guest.logger)

        guest.info('artifact installation logs are in {}'.format(logs_location))

        for i, step in enumerate(self.steps):
            guest.info(step.label)

            log_filename = '{}-{}.txt'.format(i, step.label.replace(' ', '-'))
            log_filepath = os.path.join(self.log_dirpath, log_filename)

            command = step.command
            # replace yum with dnf in case yum is not present on guest
            if not yum_present and command.startswith('yum'):
                command = '{}{}'.format('dnf', command[3:])

            if not step.items:
                command_failed, error_message = _run_and_log(command, log_filepath, step.callback)

                if command_failed and not step.ignore_exception:
                    raise SUTInstallationFailedError(
                        self.primary_task,
                        guest,
                        reason=error_message,
                        installation_logs=self.log_dirpath,
                        installation_logs_location=logs_location
                    )

            for item in step.items:
                # `step.command` contains `{}` to indicate place where item is substitute.
                # e.g 'yum install -y {}'.format('ksh')
                final_command = command.format(item)

                command_failed, error_message = _run_and_log(final_command, log_filepath, step.callback)

                if not command_failed:
                    continue

                if step.ignore_exception:
                    continue

                if error_message:
                    self.logger.error(error_message)
                    raise SUTInstallationFailedError(
                        self.primary_task,
                        guest,
                        items=item,
                        reason=error_message,
                        installation_logs=self.log_dirpath,
                        installation_logs_location=logs_location
                    )

                raise SUTInstallationFailedError(
                    self.primary_task,
                    guest,
                    items=item,
                    installation_logs=self.log_dirpath,
                    installation_logs_location=logs_location
                )

        guest.info('All packages have been successfully installed')


def check_ansible_sut_installation(ansible_output,  # type: Dict[str, Any]
                                   guest,  # type: libci.guest.NetworkedGuest
                                   primary_task,  # type: Any
                                   logger=None  # type: Optional[gluetool.log.ContextAdapter]
                                  ):  # noqa
    # type: (...) -> None
    """
    Checks json output of ansible call. Raises ``SUTInstallationFailedError`` if some of
    ansible installation tasks failed.

    :param ansible_output: output (in json format) to be checked
    :param guest: guest where playbook was run
    :param primary_task: Object covering installed artifact
    :param logger: Logger object used to log
    :raises SUTInstallationFailedError: if some of ansible installation tasks failed
    """

    logger = logger or gluetool.log.Logging.get_logger()

    log_dict(logger.debug,
             'ansible output before jq processing',
             ansible_output)

    query = """
          .plays[].tasks[].hosts
        | to_entries[]
        | select(.value.results != null)
        | {
            host: .key,
            items: [
                  .value.results[]
                | select(.failed==true)
                | .item
            ]
          }
        | select(.items != [])""".replace('\n', '')

    failed_tasks = jq(query).transform(ansible_output, multiple_output=True)

    log_dict(logger.debug,
             'ansible output after jq processing',
             failed_tasks)

    if not failed_tasks:
        return

    first_fail = failed_tasks[0]
    failed_modules = first_fail['items']

    guest.warn('Following items have not been installed: {}'.format(','.join(failed_modules)))
    raise SUTInstallationFailedError(primary_task, guest, failed_modules)
