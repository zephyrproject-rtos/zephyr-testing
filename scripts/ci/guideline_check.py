#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2021 Intel Corporation

import os
import sh
import argparse
import re
from unidiff import PatchSet
import json
import sys


if "ZEPHYR_BASE" not in os.environ:
    exit("$ZEPHYR_BASE environment variable undefined.")

RESERVED_NAMES_SCRIPT = "/scripts/coccinelle/reserved_names.cocci"

coccinelle_scripts = [RESERVED_NAMES_SCRIPT,
                      "/scripts/coccinelle/same_identifier.cocci",
                      #"/scripts/coccinelle/identifier_length.cocci",
                      ]

coccinelle_reserved_names_exclude_regex = [
    r"lib/libc/.*",
    r"lib/posix/.*",
    r"include/zephyr/posix/.*",
]

def parse_coccinelle(contents: str, violations: dict):
    reg = re.compile("([a-zA-Z0-9_/]*\\.[ch]:[0-9]*)(:[0-9\\-]*: )(.*)")
    for line in contents.split("\n"):
        r = reg.match(line)
        if r:
            f = r.group(1)
            if f in violations:
                violations[f].append(r.group(3))
            else:
                violations[r.group(1)] = [r.group(3)]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Check commits against Cocccinelle rules", allow_abbrev=False)
    parser.add_argument('-r', "--repository", required=False,
                        help="Path to repository")
    parser.add_argument('-c', '--commits', default=None,
                        help="Commit range in the form: a..b")
    parser.add_argument("-o", "--output", required=False,
                        help="Print violation into a file")
    parser.add_argument("-s", "--sarif", required=False,
                        help="Genrate sarif file")
    return parser.parse_args()

class CocciToSarif:

    def __init__(self, tags=[]):
        self.tags = tags
        self.results = []
        self.rules = {}


    def add_rule(self, rule_id, short_description, full_description,
                 help_text, level, tags=[]):
        self.rules[rule_id] = {
                    "id": rule_id,
                    # This appears as the title on the list and individual issue view
                    "shortDescription": {"text": short_description},
                    # This appears as a sub heading on the individual issue view
                    "fullDescription": {"text": full_description},
                    # This appears on the individual issue view in an expandable box
                    "help": {
                        "markdown": full_description,
                        # This property is not used if markdown is provided, but is required
                        "text": "",
                    },
                    "defaultConfiguration": {"level": level},
                    "properties": {"tags": tags},
                }

    def add_result(self, rule_id, target_file, line, message):
            result = {
                "ruleId": rule_id,
                # This appears in the line by line highlight on the individual issue view
                "message": {"text": message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {
                                    "uri": target_file
                                },
                            "region": {
                                "startLine": line
                                },
                        }
                    }
                ],
            }
            self.results.append(result)

    def get_sarif(self):
        output = {
            "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                                "name": "Cocci",
                                "rules": self.rules
                            }
                        },
                    "results": self.results,
                }
            ],
        }
        return output


def main():
    args = parse_args()

    if args.sarif:
        csarif = CocciToSarif()
        csarif.add_rule(
            rule_id="zephyr.rule_21.1",
            short_description="Should not used a reserved identifier",
            full_description="Should not used a reserved identifier",
            help_text="Should not used a reserved identifier",
            level="error",
            tags=["cocci", "zephyr"]
        )

    if not args.commits:
        exit("missing commit range")

    if args.repository is None:
        repository_path = os.environ['ZEPHYR_BASE']
    else:
        repository_path = args.repository

    sh_special_args = {
        '_tty_out': False,
        '_cwd': repository_path
    }

    # pylint does not like the 'sh' library
    # pylint: disable=too-many-function-args,unexpected-keyword-arg
    commit = sh.git("diff", args.commits, **sh_special_args)
    patch_set = PatchSet(commit)
    zephyr_base = os.getenv("ZEPHYR_BASE")
    violations = {}
    numViolations = 0

    for f in patch_set:
        if not f.path.endswith(".c") and not f.path.endswith(".h") or not os.path.exists(zephyr_base + "/" + f.path):
            continue

        for script in coccinelle_scripts:

            skip_reserved_names = False
            if script == RESERVED_NAMES_SCRIPT:
                for path in coccinelle_reserved_names_exclude_regex:
                    if re.match(path, f.path):
                        skip_reserved_names = True
                        break

            if skip_reserved_names:
                continue

            script_path =zephyr_base + "/" + script
            print(f"Running {script} on {f.path}")
            try:
                cocci = sh.coccicheck(
                    "--mode=report",
                    "--cocci=" +
                    script_path,
                    f.path,
                    _timeout=10,
                    **sh_special_args)
                parse_coccinelle(cocci, violations)
            except sh.TimeoutException:
                print("we timed out waiting, skipping...")

        for hunk in f:
            for line in hunk:
                if line.is_added:
                    violation = f"{f.path}:{line.target_line_no}"
                    if violation in violations:
                        numViolations += 1

                        if args.sarif:
                            csarif.add_result(
                                rule_id="zephyr.rule_21.1",
                                target_file=f.path,
                                line=line.target_line_no,
                                message="\t\n".join(violations[violation]))
                        elif args.output:
                            with open(args.output, "a+") as fp:
                                fp.write("{}:{}\n".format(
                                    violation, "\t\n".join(
                                        violations[violation])))
                        else:
                            print(
                                "{}:{}".format(
                                    violation, "\t\n".join(
                                        violations[violation])))

    if args.sarif:
        csarif_output = csarif.get_sarif()
        with open(args.sarif, "w") as fp:
            json.dump(csarif_output, fp, indent=4)
        print("sarif file generated")
    return numViolations


if __name__ == "__main__":
    ret = main()
    exit(ret)
