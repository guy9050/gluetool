import collections
import re
import os
import shlex
import shutil
import stat
import tempfile

import gluetool
from gluetool.action import Action
from gluetool.log import log_xml
from gluetool.result import Result
from gluetool.utils import Command
from gluetool_modules.libs import create_inspect_callback


DEFAULT_RESTRAINTD_PORT = 8081

DEFAULT_RESTRAINTD_START_TIMEOUT = 30
DEFAULT_RESTRAINTD_START_TIMEOUT_TICK = 10


#: Represents bundle of information we know about restraint's output
#:
#: :ivar gluetool.utils.ProcessOutput execution_output: raw output of the command
#:     as returned by :py:meth:`gluetool.utils.Command.run`.
#: :ivar str directory: path to a directory with ``restraint`` files.
RestraintOutput = collections.namedtuple('RestraintOutput', ('execution_output', 'directory'))


class Restraint(gluetool.Module):
    """
    Provides the very basic access to ``restraint``. Give its shared function a job description
    (in XML), and receive ``restraint``'s output.
    """

    name = 'restraint'
    description = 'Provides basic access to restraint client.'

    options = {
        'restraint-options': {
            'help': 'Additional restraint options (default: %(default)s).',
            'default': None
        },
        'start-restraintd': {
            'help': """
                    If set, start ``restraintd`` service and wait for it to become available before running tests
                    (default: %(default)s).
                    """,
            'metavar': 'yes|no',
            'type': str,
            'default': 'yes'
        },
        'restraintd-start-timeout': {
            'help': 'Wait SECONDS for restraintd to start and listen (default: %(default)s)',
            'type': int,
            'default': DEFAULT_RESTRAINTD_START_TIMEOUT,
            'metavar': 'SECONDS'
        },
        'restraintd-start-timeout-tick': {
            'help': 'To pass ``restraintd-start-timeout``, check every SECONDS (default: %(default)s)',
            'type': int,
            'default': DEFAULT_RESTRAINTD_START_TIMEOUT_TICK,
            'metavar': 'SECONDS'
        },
        'restraintd-port': {
            'help': 'Port on which ``restraintd`` is waiting for the tests (default: %(default)s).',
            'type': int,
            'default': DEFAULT_RESTRAINTD_PORT,
            'metavar': 'PORT'
        }
    }

    shared_functions = ('restraint',)

    def sanity(self):
        gluetool.utils.check_for_commands(['restraint'])

    def _guest_restraint_address(self, guest):
        if gluetool.utils.normalize_bool_option(self.option('start-restraintd')):
            return '{}:{}/{}'.format(guest.hostname, self.option('restraintd-port'), guest.port)

        return guest.hostname

    def _spawn_restraintd(self, guest, parent_action):
        # Make sure restraintd is running and listens for connections
        try:
            with Action(
                'starting restraintd',
                parent=parent_action,
                logger=guest.logger,
                tags={
                    'guest': {
                        'hostname': guest.hostname,
                        'environment': guest.environment.serialize_to_json()
                    }
                }
            ):
                guest.execute('service restraintd start')

        except gluetool.GlueCommandError as exc:
            raise gluetool.GlueError('Failed to start restraintd service: {}'.format(exc.output.stderr))

        def _check_restraintd_running():
            try:
                output = guest.execute('ss -lpnt | grep restraintd',
                                       connection_timeout=self.option('restraintd-start-timeout-tick'))

            except gluetool.GlueCommandError:
                return Result.Error('ss check failed, ignoring error')

            # ss' output looks like this:
            # LISTEN     0      5      127.0.0.1:\d+      *:*       users:(("restraintd",pid=\d+,fd=\d+))
            # just match the important bits, address and name. If the output matches, it's good, restraintd
            # is somewhere in the output (using search - match matches from the first character).
            match = re.search(
                r'.*?\s+127\.0\.0\.1:{}.*?"restraintd".*?'.format(self.option('restraintd-port')),
                output.stdout.strip()
            )

            return Result.Ok(True) if match is not None else Result.Error('no restraintd running')

        with Action(
            'waiting for restraintd running',
            parent=parent_action,
            logger=guest.logger,
            tags={
                'guest': {
                    'hostname': guest.hostname,
                    'environment': guest.environment.serialize_to_json()
                }
            }
        ):
            guest.wait(
                'restraintd is running',
                _check_restraintd_running,
                timeout=self.option('restraintd-start-timeout'),
                tick=self.option('restraintd-start-timeout-tick')
            )

    def restraint(self, guest, job, rename_dir_to=None):
        """
        Run a job on the guest.

        :param gluetool_modules.libs.guest.Guest guest: guest to use for running tests.
        :param job: <job /> element describing the test job.
        :param str rename_dir_to: if set, when ``restraint`` finishes, its output directory
            would be renamed to this value.
        :rtype: RestraintOutput(gluetool.utils.ProcessOutput, str, str)
        :returns: output of ``restraint`` command, a path to ``restraint`` directory, and
            and location of ``index.html`` report, suitable for reporting.
        """

        log_xml(guest.debug, 'Job', job)

        parent_action = Action.current_action()

        if gluetool.utils.normalize_bool_option('start-restraintd'):
            self._spawn_restraintd(guest, parent_action)

        restraint_command = Command(['restraint'], options=['-v'], logger=guest.logger)

        if self.option('restraint-options'):
            restraint_command.options += shlex.split(self.option('restraint-options'))

        # Write out our job description, and tell restraint to run it
        with tempfile.NamedTemporaryFile() as f:
            f.write(job.prettify(encoding='utf-8'))
            f.flush()

            try:
                remote = '1={}@{}'.format(guest.username, self._guest_restraint_address(guest))

                restraint_command.options += [
                    '--host', remote,
                    '--job', f.name
                ]

                with Action(
                    'running restraint',
                    parent=parent_action,
                    logger=guest.logger,
                    tags={
                        'guest': {
                            'hostname': guest.hostname,
                            'environment': guest.environment.serialize_to_json()
                        },
                        'options': restraint_command.options
                    }
                ):
                    output = restraint_command.run(
                        inspect=True,
                        inspect_callback=create_inspect_callback(guest.logger)
                    )

            except gluetool.GlueCommandError as exc:
                output = exc.output

        # Results are stored in a temporary directory which is logged on the first line of restraint's output.
        # Extract it for our customers.
        header_line = output.stdout.split('\n')[0].strip()
        if not header_line.startswith('Using ./tmp'):
            raise gluetool.GlueError('Don\'t know where to find restraint results')

        output_dir = os.path.join('.', header_line.split(' ')[1].strip())
        self.debug("output directory seems to be '{}'".format(output_dir))

        # If asked to do so, rename the output directory
        if rename_dir_to:
            try:
                shutil.move(output_dir, rename_dir_to)

            except Exception as exc:
                raise gluetool.GlueError('Failed to rename restraint output directory: {}'.format(exc))

            output_dir = rename_dir_to

            self.debug("output directory renamed, now it is '{}'".format(output_dir))

        # Construct location - path or URL - of the index.html
        index_location = '{}/index.html'.format(output_dir)
        self.debug("local index location is '{}'".format(index_location))

        # Woraround time! Restraint creates some directories and files, accessible, everything but index.html.
        # index.html is set to u=rw,go=. Let's make it at least u=rw,go=r.
        if os.path.exists(index_location):
            try:
                os.chmod(index_location, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

            except Exception as exc:
                raise gluetool.GlueError('Failed to change permissions of results index: {}'.format(exc))

        else:
            self.warn('No results index produced by restraint, cannot fix its permissions')

        return RestraintOutput(output, output_dir)
