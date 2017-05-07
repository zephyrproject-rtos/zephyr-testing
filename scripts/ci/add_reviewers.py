#! /usr/bin/env python
#
# Copyright (c) 2017 Intel Corporation
#
# SPDX-License-Identifier: Apache-2.0
#
"""
Misc helper functions used across CI integration
"""

import argparse
import collections
import csv
import logging
import os
import re
import subprocess
import sys

commit_range = os.environ['COMMIT_RANGE']

_gm_regex = re.compile(r"(?P<name>.+)\s+<(?P<email>[^@<>\s]+@[^@<>\s]+)>\s+\((?P<data>[^\)]+)\)")
_data_regex = re.compile(r"(authored:[0-9]+/[0-9]+=(?P<authored>[0-9]+)%" \
                         "|added_lines:[0-9]+/[0-9]+=(?P<added_lines>[0-9]+)%" \
                         "|removed_lines:[0-9]+/[0-9]+=(?P<removed_lines>[0-9]+)%)")

def file_find_maintainers(commits, wd = None,
                          gm_path = "./scripts/get_maintainer.pl",
                          gm_opts = [ "--nos", "--nol" ]):
    """
    Given a file, find who are its maintainers and return a dictionary
    sorted by who is most likely to be more active.

    The MAINTAINERS database is assumed to be in `wd/MAINTAINERS`. The

    Activity is determined by sorting how many modifications the
    maintianer has done (additions), being listed as supporter vs
    generic catch all, etc.

    :param str commits: range of commits to operate on
    :param str wd: working directory (defaults to current working dir)
    :param str gm_path: get_maintainers.pl command (optional)
    :param str gm_path: get_maintainers.pl options (optional)
    :returns: sorted dictionary keyed by author's email, values being
      the name and the percent weights assigned to his contributions:

      [EMAIL] = (NAME, (ADDED, MODIFIED, EXTRA, REMOVED))

      sorting is done by the weights, with highest contributor first
    """
    if wd:
        os.chdir(wd)

    ps = subprocess.Popen(('git', 'diff', commits), stdout=subprocess.PIPE)
    o = subprocess.check_output( [ gm_path ] + gm_opts, universal_newlines = True, stdin=ps.stdout)
    authors = {}
    for line in o.splitlines():
        if line == "":
            continue
        # each line is of the form
        # NAME <EMAIL> (DATA)
        authored = 0
        added = 0
        removed = 0
        extra = 0
        m = _gm_regex.match(line)
        if not m:
            logging.error("%s: can't parse get_maintainer line: %s",
                          filename, line)
            continue
        gd = m.groupdict()
        name = gd['name']
        email = gd['email']
        # Parse the data to gather numericals that sort
        for item in gd['data'].split(','):
            # Each DATA entry is of the form ITEM[,ITEM[,ITEM[,...]]]
            # Each ITEM of the form:
            #   - supporter:SUBSYSTEM
            #   - chief penguin:SUBSYSTEM
            #   - authored:5/30=17%
            #   - added_lines:5/30=17%
            #   - removed_lines:5/30=17%
            # Extract the added/removed/authored percentages;
            # assign numerical values to supporter and chief penguin.
            m = _data_regex.match(item)
            if m:
                dgd = m.groupdict()
                if dgd['added_lines']:
                    added = int(dgd['added_lines'])
                elif dgd['removed_lines']:
                    removed = int(dgd['removed_lines'])
                elif dgd['authored']:
                    authored = int(dgd['authored'])
            elif 'supporter' in item:
                extra = 40
            elif 'chief penguin' in item:
                extra = 20
            else:
                logging.error("%s: unknown data type in get_maintainer "
                              "data line: %s", filename, gd['data'])
        # Now, each author gets a vector; the order implies
        # what gets him more weigth to get an assignment.
        authors[email] = (name, (extra, authored, added, removed))
    return collections.OrderedDict(sorted(authors.items(), reverse = True,
                                          key = lambda item: item[1][1]))


out = file_find_maintainers(commit_range)
print out
for i in out:
    print i
