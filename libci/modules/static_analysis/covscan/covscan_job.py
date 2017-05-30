import os
from libci import Module
from libci import CIError
from libci import utils
from libci.utils import run_command
from jenkinsapi.custom_exceptions import UnknownJob


# Jenkins Job Builder YAML
JOB_NAME = 'ci-covscan'
JJB_YAML = 'ci-covscan.yaml'

# required cmdline tools
REQUIRED_CMDS = ['jenkins-jobs']


class CICovscanJob(Module):

    name = 'covscan-job'
    description = 'Create and run covscan job'

    options = {
        'id': {
            'help': 'Brew task id',
        },
        'notify-recipients-options': {
            'help': 'Additional options for notify-recipients module',
            'action': 'append',
            'default': []
        },
        'notify-email-options': {
            'help': 'Additional options for notify-email module'
        }
    }

    jenkins = None
    brew_id = None
    job_name = None
    yaml = None

    def sanity(self):
        # check for jjb
        utils.check_for_commands(REQUIRED_CMDS)

        self.job_name = JOB_NAME
        self.yaml = os.path.join(self.data_path, JJB_YAML)

        # check if jjb yaml exists
        if not os.path.exists(self.yaml):
            raise CIError('job yaml not found in \'{}\''.format(self.yaml))

        # check for available id
        self.brew_id = self.option('id') or os.environ['id']
        if not self.brew_id:
            raise CIError('id not found in environment')

    def update_job(self):
        run_command(['jenkins-jobs', '--flush-cache', 'update', self.yaml])

        # reconnect to jenkins
        self.jenkins = self.shared('jenkins', reconnect=True)

    def execute(self):
        self.jenkins = self.shared('jenkins')
        if self.jenkins is None:
            raise CIError('no jenkins connection found')

        notify_recipients_options = self.option('notify-recipients-options')
        if notify_recipients_options:
            notify_recipients_options = ' '.join(notify_recipients_options)

        else:
            notify_recipients_options = None

        try:
            self.jenkins[self.job_name]
        except UnknownJob:
            self.update_job()

        build_params = {
            'id': self.brew_id,
            'notify_recipients_options': notify_recipients_options,
            'notify_email_options': self.option('notify-email-options')
        }

        self.jenkins[self.job_name].invoke(build_params=build_params)
        self.info("invoked job '{}' with build params id='{}'".format(self.job_name, self.brew_id))
