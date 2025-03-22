#!/usr/bin/env python3

import json
import os
import re
import sys
from pathlib import Path
from collections import defaultdict
import argparse

if "ZEPHYR_BASE" not in os.environ:
    exit("$ZEPHYR_BASE environment variable undefined.")

# These are globally used variables. They are assigned in __main__ and are visible in further methods
# however, pylint complains that it doesn't recognize them when used (used-before-assignment).
zephyr_base = Path(os.environ['ZEPHYR_BASE'])

sys.path.insert(0, os.path.join(zephyr_base / "scripts"))
from get_maintainer import Maintainers

def load_database(database_path):
    with open(database_path, 'r') as file:
        return json.load(file)

def find_areas(files):
    maintf = zephyr_base / "MAINTAINERS.yml"
    maintainer_file = Maintainers(maintf)

    num_files = 0
    all_areas = set()

    for changed_file in files:
        num_files += 1
        print(f"file: {changed_file}")
        areas = maintainer_file.path2areas(changed_file)

        if not areas:
            continue
        all_areas.update(areas)
    tests = []
    for area in all_areas:
        for suite in area.tests:
            tests.append(f"{suite}.*")
    return tests

def find_best_coverage(database, changed_files, tests):
    coverage = defaultdict(lambda: defaultdict(int))

    for file in changed_files:
        if file in database:
            for entry in database[file]:
                if not any(re.search(f"^{test}", entry["testsuite_id"]) for test in tests):
                    print("skip")
                    continue
                testsuite_id = entry["testsuite_id"]
                platform = entry["platform"]
                coverage[testsuite_id][platform] += 1

    best_coverage = []
    for testsuite_id, platforms in coverage.items():
        for platform, count in platforms.items():
            best_coverage.append((testsuite_id, platform, count))

    best_coverage.sort(key=lambda x: x[2], reverse=True)
    return best_coverage

def main(database_path, changed_files):
    tests = find_areas(changed_files)
    database = load_database(database_path)
    best_coverage = find_best_coverage(database, changed_files, tests)

    if best_coverage:
        print("Best coverage testsuites and platforms:")
        for testsuite_id, platform, count in best_coverage:
            print(f"Testsuite: {testsuite_id}, Platform: {platform}, Coverage: {count}")
    else:
        print("No matching testsuites found for the provided files.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find the best coverage testsuites and platforms for changed files.")
    parser.add_argument("--database", help="Path to the testsuite database JSON file")
    parser.add_argument("--changed-files", action="append", help="List of changed files")

    args = parser.parse_args()

    main(args.database, args.changed_files)
