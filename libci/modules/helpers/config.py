from libci import Module
from libci import libciError
import os
import tarfile


class CIConfig(Module):
    """
Configuration module provides various common configurations for citool.
"""

    name = 'config'
    description = 'Configure citool'

    options = {
        'file': {
            'help': 'Extranct given configuration to user\'s home dir',
        }
    }

    def execute(self):
        fname = self.option('file')

        if not fname:
            try:
                dc = os.listdir(self.data_path)
                if not dc:
                    msg = 'no configuration files found in '
                    msg += '\'{}\''.format(self.data_path)
                else:
                    msg = 'available configuration files:\n'
                    msg += '\n'.join(dc)
            except TypeError:
                    msg = 'no configuration files found'
            self.info(msg)
            return

        # check if config file exists
        fpath = os.path.join(self.data_path, fname)
        if not os.path.exists(fpath):
            msg = 'configuration file \'{}\' not found'.format(fpath)
            raise libciError(msg)

        # extract configuration
        tdir = os.path.expanduser('~')
        tar = tarfile.open(fpath)
        tar.extractall(path=tdir)
        tar.close()

        self.info('file \'{}\' extracted to \'{}\''.format(fname, tdir))

