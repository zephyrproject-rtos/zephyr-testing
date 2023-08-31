# Copyright (c) 2020 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

"""
Like gen_kconfig_rest.py, but for generating an index of existing
west projects.
"""

import argparse
from collections import defaultdict
import glob
import io
import logging
import os
from pathlib import Path
import pprint
import re
import sys
import textwrap

import gen_helpers

from west.manifest import ImportFlag, Manifest, MANIFEST_PROJECT_INDEX

ZEPHYR_BASE = Path(__file__).parents[2]

logger = logging.getLogger('gen_west_projects_rest')

def load_manifest():
    m = Manifest.from_topdir()
    return m
    for p in m.projects[MANIFEST_PROJECT_INDEX:]:
        if m.is_active(p):
            print(f'{p.name} is active')

    return m.projects[MANIFEST_PROJECT_INDEX:]

def main():
    args = parse_args()
    setup_logging(args.verbose)
    manifest = load_manifest()
    dump_content(manifest, args.out_dir, args.turbo_mode)

def parse_args():
    # Parse command line arguments from sys.argv.

    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('-v', '--verbose', default=0, action='count',
                        help='increase verbosity; may be given multiple times')
    parser.add_argument('--turbo-mode', action='store_true',
                        help='Enable turbo mode (dummy references)')
    parser.add_argument('out_dir', help='output files are generated here')

    return parser.parse_args()

def setup_logging(verbose):
    if verbose >= 2:
        log_level = logging.DEBUG
    elif verbose == 1:
        log_level = logging.INFO
    else:
        log_level = logging.ERROR
    logging.basicConfig(format='%(filename)s:%(levelname)s: %(message)s',
                        level=log_level)



def dump_content(manifest, out_dir, turbo_mode):
    out_dir = Path(out_dir)

    setup_project_dir(out_dir)
    if turbo_mode:
        write_dummy_index(manifest, out_dir)
    else:
        write_projects_rst(manifest, out_dir)
        write_orphans(manifest, out_dir)

def setup_project_dir(out_dir):
    # Make a set of all the Path objects we will be creating for
    # out_dir / projects / {project_path}.rst. Delete all the ones that
    # shouldn't be there. Make sure the project output directory
    # exists.

    paths = set()
    projects_dir = out_dir / 'projects'
    logger.info('making output subdirectory %s', projects_dir)
    projects_dir.mkdir(parents=True, exist_ok=True)


def write_dummy_index(projects, out_dir):
    # Write out_dir / projects.rst, with dummy anchors

    # header
    content = '\n'.join((
        '.. _west_projects_index:',
        '',
        'Dummy Project index',
        '####################',
        '',
    ))

    content += '\n'.join((
        f'- {project.name}' for project in projects
    ))

    write_if_updated(out_dir / 'projects.rst', content)


def write_projects_rst(manifest, out_dir):
    # Write out_dir / projects.rst, the top level index of projects

    string_io = io.StringIO()

    print_block(f'''\
    :orphan:

    .. _west_projects_index:

    West Projects index
    ###################

    See :ref:`external-contributions` for more information about
    this contributing and review process for imported components.

    Active Projects/Modules
    +++++++++++++++++++++++

    The projects below are enabled by default and will be downloaded when you
    call `west update`. Many of the projects or modules listed below are
    essential for building generic Zephyr application and include among others
    hardware support for many of the platforms available in Zephyr.

    To disable any of the active modules, for example a specific HAL, use the
    following commands::

        west config manifest.project-filter -- -hal_FOO
        west update

    .. rst-class:: rst-columns
    ''', string_io)
    for project in manifest.projects[MANIFEST_PROJECT_INDEX:]:
        if manifest.is_active(project) and not project.name == 'manifest':
            print(f'- :ref:`{project.name} <west_project_{project.name}>`', file=string_io)

    print_block(f'''\n
    Inactive and Optional Projects/Modules
    ++++++++++++++++++++++++++++++++++++++


    The projects below are optional and will not be downloaded when you
    call `west update`. You can any of the the projects or modules listed below
    and use it to write application code.

    To enable any of the modules blow, use the following commands::

        west config manifest.project-filter -- +nanopb
        west update

    .. rst-class:: rst-columns
    ''', string_io)
    for project in manifest.projects[MANIFEST_PROJECT_INDEX:]:
        if not manifest.is_active(project):
            print(f'- :ref:`{project.name} <west_project_{project.name}>`', file=string_io)

    print_block(f'''\n
    External Projects/Modules
    ++++++++++++++++++++++++++


    The projects below are external and are not directly imported into the default manifest.
    To use any of the projects below, you will need to add them in the manifest directly.

    .. rst-class:: rst-columns

    - TBD

    ''', string_io)

    write_if_updated(out_dir / 'projects.rst', string_io.getvalue())

def write_orphans(manifest, out_dir):
    # These files are 'orphans' in the Sphinx sense: they are not in
    # any toctree.

    logging.info('updating :orphan: files for %d west projects', len(manifest.projects[MANIFEST_PROJECT_INDEX:]))
    num_written = 0

    for project in manifest.projects[MANIFEST_PROJECT_INDEX:]:
        string_io = io.StringIO()

        print_project_page(project, string_io)

        written = write_if_updated(out_dir / f'{project.name}.rst', 
                                   string_io.getvalue())

        if written:
            num_written += 1

    logging.info('done writing :orphan: files; %d files needed updates',
                 num_written)

def print_project_page(project, string_io):

    # :orphan:
    #
    # .. ref_target:
    #
    # Title [(on <bus> bus)]
    # ######################

    title = f'{project.name}'
    underline = '#' * len(title)

    print_block(f'''\
    :orphan:

    .. raw:: html

        <!--
        FIXME: do not limit page width until content uses another representation
        format other than tables
        -->
        <style>.wy-nav-content {{ max-width: none; !important }}</style>

    .. _west_project_{project.name}:

    {title}
    {underline}
    ''', string_io)


    print_definition('Git',
          project.url,
          string_io)
    print_definition('Path',
          project.path,
          string_io)
    print_definition('Revision',
          project.revision,
          string_io)
    if project.groups:
        print_definition('Groups',
              ', '.join(project.groups),
              string_io)
    else:
        print_definition('Groups',
              'None',
              string_io)

def print_definition(definition, block, string_io):
    wrap = textwrap.indent(block, '   ')
    print(f'{definition}:\n{wrap}', file=string_io)

def print_block(block, string_io):
    # Helper for dedenting and printing a triple-quoted RST block.
    # (Just a block of text, not necessarily just a 'code-block'
    # directive.)

    print(textwrap.dedent(block), file=string_io)

def to_code_block(s, indent=0):
    # Converts 's', a string, to an indented rst .. code-block::. The
    # 'indent' argument is a leading indent for each line in the code
    # block, in spaces.
    indent = indent * ' '
    return ('.. code-block:: none\n\n' +
            textwrap.indent(s, indent + '   ') + '\n')


def write_if_updated(path, s):
    # gen_helpers.write_if_updated() wrapper that handles logging and
    # creating missing parents, as needed.

    if not path.parent.is_dir():
        path.parent.mkdir(parents=True)
    written = gen_helpers.write_if_updated(path, s)
    logger.debug('%s %s', 'wrote' if written else 'did NOT write', path)
    return written


if __name__ == '__main__':
    main()
    sys.exit(0)
