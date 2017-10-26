"""
Heart of the "citool" script. Referred to by setuptools' entry point.
"""

import os

import gluetool.tool


DEFAULT_CITOOL_CONFIG_PATHS = [
    '/etc/citool.d/citool',
    os.path.expanduser('~/.citool.d/citool')
]


class Citool(gluetool.tool.Gluetool):
    def __init__(self, *args, **kwargs):
        super(Citool, self).__init__(*args, **kwargs)

        self.gluetool_config_paths += DEFAULT_CITOOL_CONFIG_PATHS


def main():
    app = Citool()
    app.main()
