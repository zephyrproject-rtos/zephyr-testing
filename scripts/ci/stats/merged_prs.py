#!/usr/bin/env python3
# Copyright (c) 2024 Intel Corp.
# SPDX-License-Identifier: Apache-2.0

import sys
import os
from github import Github
import argparse
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk


date_format = '%Y-%m-%d %H:%M:%S'

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False)

    parser.add_argument('--pull-request', required=True, help='pull request number', type=int)
    parser.add_argument('--repo', required=True, help='github repo')

    return parser.parse_args()

def gendata(data, index):
    for t in data:
        yield {
                "_index": index,
                "_source": t
                }

def main():
    args = parse_args()
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        sys.exit('Github token not set in environment, please set the '
                 'GITHUB_TOKEN environment variable and retry.')

    gh = Github(token)
    json_list = []
    gh_repo = gh.get_repo(args.repo)

    if args.pull_request:
        pr = gh_repo.get_pull(args.pull_request)

        reviews = pr.get_reviews()
        print(f'#{pr.number}: {pr.title} - {pr.comments} Comments, reviews: {reviews.totalCount}, {len(pr.assignees)} Assignees (Updated {pr.updated_at})')
        assignee_reviews = 0
        reviewers = set()
        for r in reviews:
            if r.user and r.state == 'APPROVED':
                reviewers.add(r.user.login)
            if pr.assignee and r.user:
                if r.user.login == pr.assignee.login:
                    assignee_reviews = assignee_reviews + 1
        prj = {}
        prj['nr'] = pr.number
        prj['url'] = pr.url
        prj['title'] = pr.title
        prj['comments'] = pr.comments
        prj['reviews'] = reviews.totalCount
        prj['assignees'] = len(pr.assignees)
        prj['updated'] = pr.updated_at.strftime("%Y-%m-%d %H:%M:%S")
        prj['created'] = pr.created_at.strftime("%Y-%m-%d %H:%M:%S")
        prj['closed'] = pr.closed_at.strftime("%Y-%m-%d %H:%M:%S")
        prj['merged_by'] = pr.merged_by.login
        prj['submitted_by'] = pr.user.login
        prj['changed_file'] = pr.changed_files
        prj['additions'] = pr.additions
        prj['deletions'] = pr.deletions
        prj['commits'] = pr.commits
        prj['base'] = pr.base.ref

        if pr.assignee:
            prj['assignee'] = pr.assignee.login
            prj['reviewed_by_assignee'] = "yes" if pr.assignee.login in reviewers else "no"
            if pr.assignee.login in reviewers or 'Hotfix' in pr.labels or pr.assignee.login == pr.user.login:
                prj['review_rule'] = "yes"
            else:
                prj['review_rule'] = "no"
        else:
            prj['assignee'] = "none"
            prj['reviewed_by_assignee'] = "na"
            prj['review_rule'] = "na"
        prj['assignee_reviews'] = assignee_reviews
        ll = []
        for l in pr.labels:
            ll.append(l.name)

        prj['labels'] = ll
        delta = pr.closed_at - pr.created_at
        deltah = delta.total_seconds() / 3600
        prj['hours_open'] = deltah

        if deltah < 48 and not ('Trivial' in ll or 'Hotfix' in ll):
            prj['time_rule'] = False
        elif deltah < 4 and 'Trivial' in ll:
            prj['time_rule'] = False
        else:
            prj['time_rule'] = True
        prj['reviewers'] = list(reviewers)

        json_list.append(prj)

    es = Elasticsearch(
            [os.environ['ELASTICSEARCH_SERVER']],
            api_key=os.environ['ELASTICSEARCH_KEY'],
            verify_certs=False
            )

    try:
        index = os.environ['PR_STAT_INDEX']
        bulk(es, gendata(json_list, index))
    except KeyError as e:
        print(f"Error: {e} not set.")

if __name__ == "__main__":
    main()
