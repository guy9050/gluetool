#!/usr/bin/env python

"""
Generate RST files documenting modules.
"""

import inspect
import os

import libci


CI = libci.CI()

CWD = os.getcwd() + '/'

with open('docs/source/modules/template.txt', 'r') as g:
    TEMPLATE = g.read()

# generate page for each module
with open('docs/source/module_parsers.py', 'w') as f:
    f.write('# pylint: disable=invalid-name,protected-access\n')

    for name, properties in CI.modules.iteritems():
        # get file where class is stored
        filepath = inspect.getfile(properties['class'])

        # strip the CWD out
        filepath = filepath.replace(os.path.commonprefix([CWD, filepath]), '')

        # cut away the extension
        filepath = os.path.splitext(filepath)[0]

        # convert it to a Python module path
        modpath = filepath.replace('/', '.')

        description = properties['description'] if properties['description'] is not None else ''

        variables = {
            'name': name,
            'description': description,
            'title_underline': '=' * (2 + len(name) + 4 + len(description)),
            'full_path': '{}.{}'.format(modpath, properties['class'].__name__),
            'modpath': modpath,
            'klass': properties['class'].__name__
        }

        with open('docs/source/modules/{}.rst'.format(name), 'w') as g:
            g.write(libci.utils.render_template(TEMPLATE, **variables))
            g.flush()

        f.write("""

def get_parser_{klass}():
    from {modpath} import {klass}
    return {klass}._create_args_parser()
""".format(**variables))

    f.flush()

# generate index page
with open('docs/source/modules.txt', 'r') as f:
    with open('docs/source/modules.rst', 'w') as g:
        g.write(f.read().format(modules='\n'.join(sorted([
            '   modules/{}'.format(name) for name in CI.modules.iterkeys()
        ]))))
        g.flush()
