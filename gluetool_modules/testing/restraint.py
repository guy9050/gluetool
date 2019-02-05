import collections
import re
import os
import shlex
import shutil
import tempfile

import gluetool
from gluetool.log import log_xml, ContextAdapter
from gluetool.utils import Bunch, Command

DEFAULT_RESTRAINTD_PORT = 8081

DEFAULT_RESTRAINTD_START_TIMEOUT = 30
DEFAULT_RESTRAINTD_START_TIMEOUT_TICK = 10


#: Represents bundle of information we know about restraint's output
#:
#: :ivar gluetool.utils.ProcessOutput execution_output: raw output of the command
#:     as returned by :py:meth:`gluetool.utils.Command.run`.
#: :ivar str directory: path to a directory with ``restraint`` files.
#: :ivar str index_location: path - or URL if ``BUILD_URL`` env var exists - to the ``index.html``
#:     report page. Used for logging and reporting, to provide user access to this file.
RestraintOutput = collections.namedtuple('RestraintOutput', ('execution_output', 'directory', 'index_location'))


class StdStreamAdapter(ContextAdapter):
    # pylint: disable=too-few-public-methods

    def __init__(self, logger, name):
        super(StdStreamAdapter, self).__init__(logger, {'ctx_stream': (100, name)})


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
            'help': 'Port on which ``restraind`` is waiting for the tests (default: %(default)s).',
            'type': int,
            'default': DEFAULT_RESTRAINTD_PORT,
            'metavar': 'PORT'
        },
        'artifacts-location-template': {
            'help': """
                    When set, it will be rendered to provide the **final** location of artifacts, generated
                    by Restraint. It has access to common eval context, with ``ARTIFACTS_LOCATION`` representing
                    **current** - local, on the machine running the pipeline - location of ``index.html``
                    (default: %(default)s).
                    """,
            'default': None
        }
    }

    shared_functions = ('restraint',)

    def sanity(self):
        gluetool.utils.check_for_commands(['restraint'])

    def _guest_restraint_address(self, guest, restraintd_port):
        # pylint: disable=no-self-use

        return '{}:{}/{}'.format(guest.hostname, restraintd_port, guest.port)

    def restraint(self, guest, job, port=None, rename_dir_to=None, label=None):
        # pylint: disable=too-many-arguments
        """
        Run a job on the guest.

        :param libci.guest.Guest guest: guest to use for running tests.
        :param job: <job /> element describing the test job.
        :param int port: port on which ``restraind`` is waiting for the tests. The default value is set
            by ``--restraintd-port`` option.
        :param str rename_dir_to: if set, when ``restraint`` finishes, its output directory
            would be renamed to this value.
        :param str label: if set, path or URL to ``restraint`` index.html report will be logged
            using ``label`` as the log message intro.
        :rtype: RestraintOutput(gluetool.utils.ProcessOutput, str, str)
        :returns: output of ``restraint`` command, a path to ``restraint`` directory, and
            and location of ``index.html`` report, suitabel for reporting.
        """

        log_xml(guest.debug, 'Job', job)

        port = port or self.option('restraintd-port')

        # Make sure restraintd is running and listens for connections
        guest.execute('service restraintd start')

        def _check_restraintd_running():
            try:
                output = guest.execute('ss -lpnt | grep restraintd',
                                       connection_timeout=self.option('restraintd-start-timeout-tick'))

            except gluetool.GlueCommandError:
                self.debug('ss check failed, ignoring error')
                return False

            # ss' output looks like this:
            # LISTEN     0      5      127.0.0.1:\d+      *:*       users:(("restraind",pid=\d+,fd=\d+))
            # just match the important bits, address and name. If the output matches, it's good, restraind
            # is somewhere in the output (using search - match matches from the first character).
            return re.search(r'.*?\s+127\.0\.0\.1:{}.*?"restraintd".*?'.format(port), output.stdout.strip()) is not None

        guest.wait('restraintd is running', _check_restraintd_running,
                   timeout=self.option('restraintd-start-timeout'), tick=self.option('restraintd-start-timeout-tick'))

        restraint_command = [
            'restraint', '-v'
        ]

        if self.option('restraint-options'):
            restraint_command += shlex.split(self.option('restraint-options'))

        # Write out our job description, and tell restraint to run it
        with tempfile.NamedTemporaryFile() as f:
            f.write(job.prettify(encoding='utf-8'))
            f.flush()

            stdout_logger = StdStreamAdapter(guest.logger, 'stdout')
            stderr_logger = StdStreamAdapter(guest.logger, 'stderr')

            class StreamHandler(Bunch):
                # pylint: disable=too-few-public-methods

                def write(self):
                    # pylint: disable=no-member,attribute-defined-outside-init,access-member-before-definition
                    self.logger(''.join(self.buff))
                    self.buff = []

            streams = {
                '<stdout>': StreamHandler(buff=[], logger=stdout_logger.info),
                '<stderr>': StreamHandler(buff=[], logger=stderr_logger.warn)
            }

            def output_streamer(stream, data, flush=False):
                stream_handler = streams[stream.name]

                if flush and stream_handler.buff:
                    stream_handler.write()
                    return

                if data is None:
                    return

                for c in data:
                    if c == '\n':
                        stream_handler.write()

                    elif c == '\r':
                        continue

                    else:
                        stream_handler.buff.append(c)

            try:
                remote = '1={}@{}'.format(guest.username, self._guest_restraint_address(guest, port))

                cmd = Command(restraint_command + [
                    '--host', remote,
                    '--job', f.name
                ], logger=guest.logger)

                output = cmd.run(inspect=True, inspect_callback=output_streamer)

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
                raise gluetool.GlueError('Failed to rename restraitn output directory: {}'.format(exc))

            output_dir = rename_dir_to

            self.debug("output directory renamed, now it is '{}'".format(output_dir))

        # Construct location - path or URL - of the index.html
        index_location = '{}/index.html'.format(output_dir)
        self.debug("local index location is '{}'".format(index_location))

        if self.option('artifacts-location-template'):
            index_location = gluetool.utils.render_template(
                self.option('artifacts-location-template'),
                logger=self.logger,
                ARTIFACTS_LOCATION=index_location,
                **self.shared('eval_context')
            )

            # The rendered location may be URL, but also it may be something completely different.
            # Try to treat it like the URL, but ignore failures - ``treat_url`` would fail when
            # the string didn't start with schema, for example.
            try:
                index_location = gluetool.utils.treat_url(index_location, logger=self.logger)

            # pylint: disable=bare-except
            except:
                pass

        self.debug("final index location is '{}'".format(index_location))

        # If asked to do so, log index.html location
        label = label or 'restraint logs are in'
        self.info('{} {}'.format(label, index_location))

        return RestraintOutput(output, output_dir, index_location)
