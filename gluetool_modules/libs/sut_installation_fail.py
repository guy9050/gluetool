import gluetool
from gluetool.log import log_dict
from gluetool import SoftGlueError
from libci.sentry import PrimaryTaskFingerprintsMixin
# pylint: disable=no-name-in-module
from jq import jq


class SUTInstallationFailedError(PrimaryTaskFingerprintsMixin, SoftGlueError):
    def __init__(self, task, guest, items):
        super(SUTInstallationFailedError, self).__init__(task, 'SUT installation failed')

        self.guest = guest
        self.items = items


def check_ansible_sut_installation(ansible_output, guests, primary_task, logger=None):
    """
    Checks json output of ansible call. Raises ``SUTInstallationFailedError`` if some of
    ansible installation tasks failed.

    :param ansible_output: output (in json format) to be checked
    :param guests: list of guests, where playbook was run
    :param primary_task: Object covering installed artifact
    :param logger: Logger object used to log
    :raises SUTInstallationFailedError: if some of ansible installation tasks failed
    """

    logger = logger or gluetool.log.Logging.get_logger()
    log_dict(logger.debug, 'ansible output before jq processing', ansible_output)

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

    log_dict(logger.debug, 'ansible output after jq processing', failed_tasks)

    if not failed_tasks:
        return

    first_fail = failed_tasks[0]
    guest = [guest for guest in guests if guest.hostname == first_fail['host']][0]
    failed_modules = first_fail['items']

    guest.warn('Following items have not been installed: {}'.format(','.join(failed_modules)))
    raise SUTInstallationFailedError(primary_task, guest, failed_modules)
