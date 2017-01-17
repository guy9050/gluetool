"""
Configuration module providing various common configurations for citool.
"""

import os
import tarfile

from libci import Module, CiError


class CIConfig(Module):
    """
    Configuration module providing various common configurations for citool.
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
                config_files = os.listdir(self.data_path)
                if not config_files:
                    self.info('no configuration files found in \'{0}\''.format(self.data_path))

                else:
                    self.info('available configuration files:\n')
                    for config_file in config_files:
                        self.info('  ' + config_file)

            except TypeError:
                self.info('no configuration files found')

            return

        # check if config file exists
        fpath = os.path.join(self.data_path, fname)
        if not os.path.exists(fpath):
            msg = 'configuration file \'{}\' not found'.format(fpath)
            raise CiError(msg)

        # extract configuration
        tdir = os.path.expanduser('~')
        tar = tarfile.open(fpath)
        tar.extractall(path=tdir)
        tar.close()

        self.info('file \'{}\' extracted to \'{}\''.format(fname, tdir))
