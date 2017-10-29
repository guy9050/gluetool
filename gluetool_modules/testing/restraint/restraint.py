import shlex
import tempfile

import gluetool
from gluetool.log import log_xml, ContextAdapter
from gluetool.utils import Bunch


DEFAULT_RESTRAINT_PORT = 8081


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
            'help': 'Additional restraint options.',
            'default': None
        }
    }

    shared_functions = ('restraint',)

    def sanity(self):
        gluetool.utils.check_for_commands(['restraint'])

    def _guest_restraint_address(self, guest, port=DEFAULT_RESTRAINT_PORT):
        # pylint: disable=no-self-use

        return '{}:{}/{}'.format(guest.hostname, port, guest.port)

    def restraint(self, guest, job, port=DEFAULT_RESTRAINT_PORT):
        """
        Run a job on the guest.

        :param libci.guest.Guest guest: guest to use for running tests.
        :param job: <job /> element describing the test job.
        :param int port: restraint port.
        """

        log_xml(guest.debug, 'Job', job)

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
                return gluetool.utils.run_command(restraint_command + [
                    '--host', '1={}@{}'.format(guest.username, self._guest_restraint_address(guest, port=port)),
                    '--job', f.name
                ], logger=guest.logger, inspect=True, inspect_callback=output_streamer)

            except gluetool.GlueCommandError as exc:
                return exc.output
