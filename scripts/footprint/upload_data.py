#!/usr/bin/env python3
# Copyright (c) 2021 Intel Corporation
#
# SPDX-License-Identifier: Apache-2.0

from anytree.importer import DictImporter
from anytree import PreOrderIter
from anytree.search  import find
importer = DictImporter()
from datetime import datetime
from dateutil.relativedelta import relativedelta
import os
import json
from git import Repo

TODAY = datetime.utcnow()
two_mon_rel = relativedelta(months=4)

from influxdb import InfluxDBClient
import glob
import argparse

influx_dsn = 'influxdb://localhost:8086/footprint_tracking'

def create_event(data, board, feature, commit, current_time, typ, application):
    footprint_data = []
    client = InfluxDBClient.from_dsn(influx_dsn)
    client.create_database('footprint_tracking')
    for d in data.keys():
        footprint_data.append({
            "measurement": d,
            "tags": {
                "board": board,
                "commit": commit,
                "application": application,
                "type": typ,
                "feature": feature
            },
            "time": current_time,
            "fields": {
                "value": data[d]
            }
        })

    client.write_points(footprint_data, time_precision='s', database='footprint_tracking')


def parse_args():
    global args
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("-d", "--data", required=True,
                        help="Data Directory")
    parser.add_argument("-z", "--zephyr-base", required=True,
                        help="Zephyr tree")

    args = parser.parse_args()


def main():
    parse_args()
    repo = Repo(args.zephyr_base)
    for hash in os.listdir(f'{args.data}'):
        client = InfluxDBClient.from_dsn(influx_dsn)
        result = client.query(f"select * from kernel where commit = '{hash}';")
        if result:
            print(f"Skipping {hash}...")
            continue
        print(f"Importing {hash}...")
        for f in glob.glob(f"{args.data}/{hash}/**/*json", recursive=True):
            meta = f.split("/")
            b = os.path.basename(f)
            if 'ram' in b:
                typ = 'ram'
                depth = 2
            else:
                typ = 'rom'
                depth = 3
            commit = meta[1]
            app = meta[2]
            feature = meta[3]
            board = meta[4]

            with open(f, "r") as fp:
                contents = json.load(fp)
                root = importer.import_(contents['symbols'])

            zr = find(root, lambda node: node.name == 'ZEPHYR_BASE')
            if not zr:
                depth = 2
            data = {}
            for node in PreOrderIter(root, maxlevel=depth):
                comp = node.name
                if comp in ['WORKSPACE', 'out']:
                    continue
                if node.name == 'ZEPHYR_BASE':
                    for subnode in PreOrderIter(node, maxlevel=2):
                        comp = subnode.name
                        if comp in ['ZEPHYR_BASE', 'out']:
                            continue
                        if subnode.children:
                            data[comp] = subnode.size
                    continue

                if node.children:
                    data[comp] = node.size

            gitcommit = repo.commit(f'{commit}')
            current_time = gitcommit.committed_datetime
            create_event(data, board, feature, commit, current_time, typ, app)

if __name__ == "__main__":
    main()
