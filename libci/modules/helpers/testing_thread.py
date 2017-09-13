import hashlib
import json
import time

import libci


DEFAULT_ID_FILE = 'testing-thread-id.json'


class TestingThread(libci.Module):
    name = 'testing-thread'
    description = 'Simple testing-thread tagging.'

    shared_functions = ('thread_id',)

    options = {
        'id': {
            'help': 'Current testing thread ID.',
            'metavar': 'ID'
        },
        'id-length': {
            'help': 'Number of hash characters used as a thread ID',
            'metavar': 'NUMBER',
            'type': int,
            'default': 12
        },
        'id-file': {
            'help': 'If set, module will store the ID in this file (default: {}).'.format(DEFAULT_ID_FILE),
            'metavar': 'PATH',
            'default': DEFAULT_ID_FILE
        }
    }

    def __init__(self, *args, **kwargs):
        super(TestingThread, self).__init__(*args, **kwargs)

        self._thread_id = None

    def thread_id(self):
        return self._thread_id

    def _create_thread_id(self, template, **variables):
        self.debug("creating a thread ID from template: '{}'".format(template))
        libci.log.log_dict(self.debug, 'variables', variables)

        s = template.format(**variables)

        sha = hashlib.sha1()
        sha.update(s)

        return sha.hexdigest()[0:self.option('id-length')]

    def sanity(self):
        if self.option('id'):
            self._thread_id = self.option('id')

            self.info('testing thread ID set to {}'.format(self._thread_id))

    def execute(self):
        if self._thread_id is not None:
            return

        fmt = ['stamp']

        variables = {
            'stamp': int(time.time())
        }

        if self.has_shared('primary_task'):
            fmt.append('brew-build')
            variables['brew-build'] = self.shared('primary_task').build_id

        self._thread_id = self._create_thread_id('-'.join(['{' + s + '}' for s in fmt]), **variables)
        self.info('testing thread ID set to {}'.format(self._thread_id))

    def destroy(self, failure=None):
        if self._thread_id is None:
            self.warn('Testing thread ID is not set')
            return

        if self.option('id-file'):
            with open(self.option('id-file'), 'w') as f:
                f.write(json.dumps(self._thread_id))
                f.flush()

        results = self.shared('results') or []

        for result in results:
            self.debug('result:\n{}'.format(result))

            if 'testing-thread-id' in result.ids:
                continue

            self.debug('adding a testing thread ID')
            result.ids['testing-thread-id'] = self._thread_id
