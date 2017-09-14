#!/usr/bin/env python

"""
Generate RST files documenting modules.
"""

import inspect
import os

import libci


LOGGER = libci.log.Logging.create_logger()

MOD_TEMPLATE = """
``{name}``
{title_underline}

**{description}.**

.. automoddesc:: {modpath}.{klass}
   :noindex:


Shared functions
----------------

{shared_functions}


Options
-------

.. argparse::
   :filename: source/module_parsers.py
   :func: get_parser_{klass}
   :prog: {name}
"""

SHARED_TEMPLATE = """
.. automethod:: {modpath}.{klass}.{shared_name}
   :noindex:

"""

ARGS_TEMPLATE = """

def get_parser_{klass}():
    from {modpath} import {klass}
    return {klass}._create_args_parser()
"""


def gather_module_data():
    LOGGER.info('gathering data on all available modules')

    ci = libci.CI()
    ci.load_modules()

    cwd = os.getcwd() + '/'
    modules = []

    for name, properties in ci.modules.iteritems():
        # get file where class is stored
        filepath = inspect.getfile(properties['class'])

        # strip the CWD out
        filepath = filepath.replace(os.path.commonprefix([cwd, filepath]), '')

        description = properties['description'] if properties['description'] is not None else ''

        modules.append({
            'name': name,
            'description': description,
            'klass': properties['class'].__name__,
            'filepath': filepath,
            'modclass': properties['class'],
            'modpath': os.path.splitext(filepath)[0].replace('/', '.'),
            'filepath_mtime': os.stat(filepath).st_mtime
        })

    return modules


def write_module_doc(module_data):
    doc_file = 'docs/source/modules/{}.rst'.format(module_data['name'])

    try:
        doc_mtime = os.stat(doc_file).st_mtime

    # pylint: disable-msg=bare-except
    except:
        doc_mtime = 0

    if module_data['filepath_mtime'] <= doc_mtime:
        LOGGER.info('skipping module {} because it was not modified'.format(module_data['name']))
        return

    module_data['title_underline'] = '=' * (4 + len(module_data['name']))

    shared_functions = module_data['modclass'].shared_functions
    if shared_functions:
        module_data['shared_functions'] = '\n'.join([
            SHARED_TEMPLATE.format(shared_name=name, **module_data) for name in shared_functions
        ])

    else:
        module_data['shared_functions'] = ''

    with open(doc_file, 'w') as f:
        f.write(libci.utils.render_template(MOD_TEMPLATE, **module_data))
        f.flush()

    LOGGER.info('module {} doc page written'.format(module_data['name']))


def write_args_parser_getters(modules):
    with open('docs/source/module_parsers.py', 'w') as f:
        f.write('# pylint: disable=invalid-name,protected-access\n')

        for module_data in modules:
            f.write(ARGS_TEMPLATE.format(**module_data))

        f.flush()


def write_index_doc(modules):
    with open('docs/source/modules.txt', 'r') as f:
        with open('docs/source/modules.rst', 'w') as g:
            g.write(f.read().format(modules='\n'.join(sorted([
                # pylint: disable=line-too-long
                '   ' + libci.utils.render_template('{name}: {description} <modules/{name}>', **module_data) for module_data in modules
            ]))))
            g.flush()


def main():
    modules = gather_module_data()

    for module_data in modules:
        write_module_doc(module_data)

    write_args_parser_getters(modules)
    write_index_doc(modules)


if __name__ == '__main__':
    main()
