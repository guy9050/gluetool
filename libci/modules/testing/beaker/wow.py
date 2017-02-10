import os
import shlex
import urlparse
import bs4
from libci import CIError, CICommandError, Module, utils
from libci.utils import run_command


REQUIRED_COMMANDS = ['bkr', 'beaker-jobwatch']


class CIWow(Module):
    """
    This module just wraps beaker workflow tomorrow and injects --distro
    from shared 'distro' function if available.
    """
    name = 'wow'

    options = {
        'wow-options': {
            'help': 'Additional options for workflow-tomorrow'
        },
        'jobwatch-options': {
            'help': 'Additional options for beaker-jobwatch'
        }
    }

    shared_functions = ['results']

    _results = []

    def results(self):
        return self._results

    def sanity(self):
        utils.check_for_commands(REQUIRED_COMMANDS)

    def execute(self):
        task = self.shared('brew_task')
        if task is None:
            raise CIError('no brew build found, did you run brew module')
        distro = self.shared('distro')

        def _command_options(name):
            opts = self.option(name)
            if opts is None or not opts:
                return []

            return shlex.split(opts)

        wow_options = _command_options('wow-options')
        jobwatch_options = _command_options('jobwatch-options')

        distro_option = ['--distro={}'.format(distro)] if distro else []
        brew_option = ['--brew-task={}'.format(task.task_id)] if task is not None else []

        whiteboard = 'CI run {} brew task id {} build target {}'.format(task.nvr, task.task_id, task.target.target)

        # wow
        command = [
            'bkr', 'workflow-tomorrow',
            '--id',
            '--whiteboard', whiteboard,
            '--no-reserve'
        ] + distro_option + brew_option + wow_options

        self.info("running 'workflow-tomorrow':\n{}".format(utils.format_command_line([command])))

        try:
            output = run_command(command)

        except CICommandError as exc:
            raise CIError("Failure during 'wow' execution: {}".format(exc.output.stderr))

        self.info('wow finished succesfully!')

        # beaker-jobwatch
        command = [
            'beaker-jobwatch',
            '--skip-broken-machines'
        ] + jobwatch_options + ['--job={}'.format(job) for job in output.stdout.split()]

        self.info("running 'beaker-jobwatch':\n{}".format(utils.format_command_line([command])))

        command = ['bash', '-c', '{} | tee beaker-jobwatch.log'.format(' '.join(command))]

        try:
            output = run_command(command, stdout=utils.PARENT, stderr=utils.PARENT)

        except CICommandError as exc:
            raise CIError("Failure during 'jobwatch' execution: {}".format(exc.output.stderr))

        self.info('beaker-jobwatch finished successfully')

        with open('beaker-jobwatch.log', 'r') as f:
            jobwatch_log = f.read().strip().split('\n')

        if jobwatch_log[-1] != 'finished successfully' \
                or not jobwatch_log[-2].startswith('duration:') \
                or not jobwatch_log[-3].startswith('https://beaker.engineering.redhat.com/matrix/'):
            raise CIError('Unable to parse beaker-jobwatch output')

        matrix_url = jobwatch_log[-3].strip()
        parsed_matrix_url = urlparse.urlparse(matrix_url)
        parsed_query = urlparse.parse_qs(parsed_matrix_url.query)

        def _process_job(job):
            self.debug('looking at job {}'.format(job))

            output = run_command(['bkr', 'job-results', '--prettyxml', 'J:{}'.format(job)])

            soup = bs4.BeautifulSoup(output.stdout, 'html.parser')

            for recipe_set in soup.find_all('recipeset', attrs={'response': 'ack'}):
                if not recipe_set.find_all('recipe', attrs={'result': 'Fail'}):
                    continue

                return False

            return True

        overall_result = all((_process_job(job) for job in parsed_query['job_ids']))

        self.info('Result of wow jobs: {}'.format('PASS' if overall_result is True else 'FAIL'))

        # Prepare result info
        result = {
            'type': 'wow',
            'result': 'PASS' if overall_result is True else 'FAIL',
            'urls': {
                'beaker_matrix': matrix_url
            }
        }

        if 'BUILD_URL' in os.environ:
            result['urls']['jenkins_job'] = os.environ['BUILD_URL']

        # Publish it
        self._results = self.shared('results') or []
        self._results.append(result)
