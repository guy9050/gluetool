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

        output = None
        try:
            output = run_command(command)

        except CICommandError as exc:
            # Check for most common causes, and raise soft error where necessary
            soft = False

            if 'No relevant tasks found in test plan' in exc.output.stderr:
                soft = True

            raise CIError("Failure during 'wow' execution: {}".format(exc.output.stderr), soft=soft)

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

        with open('beaker-jobwatch.log', 'r') as f:
            jobwatch_log = f.read().strip().split('\n')

        if len(jobwatch_log) < 3:
            raise CIError('jobwatch output is unexpectedly short')

        if not jobwatch_log[-3].startswith('https://beaker.engineering.redhat.com/matrix/'):
            raise CIError('Don\'t know where to find beaker matrix URL in jobwatch output')

        matrix_url = jobwatch_log[-3].strip()

        if jobwatch_log[-1].strip() == 'finished successfully':
            self.info('beaker-jobwatch finished successfully')

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

            overall_result = 'PASS' if all((_process_job(job) for job in parsed_query['job_ids'])) else 'FAIL'

        else:
            self.warn('beaker-jobwatch does not report successful completion')

            overall_result = 'ERROR'

        self.info('Result of wow jobs: {}'.format(overall_result))

        # Prepare result info
        result = {
            'type': 'wow',
            'result': overall_result,
            'urls': {
                'beaker_matrix': matrix_url
            }
        }

        if 'BUILD_URL' in os.environ:
            result['urls']['jenkins_job'] = os.environ['BUILD_URL']

        # Publish it
        self._results = self.shared('results') or []
        self._results.append(result)
