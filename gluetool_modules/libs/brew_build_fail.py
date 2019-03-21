from gluetool import GlueCommandError, SoftGlueError

# Type annotations
# pylint: disable=unused-import,wrong-import-order,ungrouped-imports
from typing import TYPE_CHECKING  # noqa

if TYPE_CHECKING:
    import gluetool # noqa
    import gluetool.utils # noqa


class BrewBuildFailedError(SoftGlueError):
    def __init__(self, message, output):
        # type: (str, gluetool.utils.ProcessOutput) -> None

        super(BrewBuildFailedError, self).__init__(message)
        self.output = output


def run_command(module, command, comment):
    # type: (gluetool.Module, gluetool.utils.Command, str) -> gluetool.utils.ProcessOutput
    try:
        module.info(comment)
        return command.run(inspect=True)
    except GlueCommandError as exc:
        raise BrewBuildFailedError('{} failed'.format(comment), exc.output)
