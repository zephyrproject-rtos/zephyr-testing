#!/usr/bin/env python3

# Copyright The Zephyr Project Contributors
# SPDX-License-Identifier: Apache-2.0

"""
Report the merge criteria of pull requests as a check run.

The "Merge Criteria" check run stays in progress until every criterion is met
and completes successfully afterwards. Its output lists every criterion,
whether it is met and what is still needed. Draft pull requests are not
evaluated.

Blocking labels: a pull request with any of the labels in BLOCKING_LABELS
does not meet it.

Approvals: a pull request needs at least two approvals. With assignees, one of
them must approve and none may request changes; an author who is also an
assignee counts as the assignee approval. Only the latest approving or
change-requesting review of each user with write access counts.

Review period: a pull request with the Hotfix label always meets it.
Otherwise the period starts when the pull request was last marked ready for
review, or when it was created. It lasts --system-wide-review-days business
days with the System Wide Change label, 4 hours with the Trivial label and
2 business days without either. Business days do not count Saturdays and
Sundays (UTC).

The following needs --maintainer-file, which maps the changed files to areas.
Meta areas and areas that cover a file only through a deferring file group
while another area covers it too are not counted as changed.

System wide change: a pull request changing more than --system-wide-areas
areas gets the System Wide Change label.

Area approvals: every changed area marked requires-maintainer-approval needs
the approval of one of its maintainers, unless one of them is an assignee or
the author. See the description of the key in MAINTAINERS.yml. The changed
files of an area still waiting for the approval are annotated.

With --all, every open non-draft pull request is evaluated and the check run
is only written where its output changes; this picks up review periods that
have ended.
"""

import argparse
import dataclasses
import datetime
import functools
import hashlib
import json
import os
import sys
from pathlib import Path

import github

CHECK_NAME = "Merge Criteria"
BLOCKING_LABELS = ["DNM", "DNM (manifest)", "TSC", "Architecture Review"]
MIN_APPROVALS = 2
HOTFIX_LABEL = "Hotfix"
TRIVIAL_LABEL = "Trivial"
TRIVIAL_REVIEW_PERIOD = datetime.timedelta(hours=4)
REVIEW_PERIOD_BUSINESS_DAYS = 2
SYSTEM_WIDE_LABEL = "System Wide Change"
SYSTEM_WIDE_AREAS = 5
SYSTEM_WIDE_REVIEW_BUSINESS_DAYS = 5

# Check run API limits: output text sizes and annotations per request.
OUTPUT_TITLE_LIMIT = 1000
OUTPUT_TEXT_LIMIT = 65535
ANNOTATIONS_PER_REQUEST = 50

PR_FRAGMENT = """
fragment pr on PullRequest {
  number
  url
  isDraft
  createdAt
  headRefOid
  author { login }
  headRepositoryOwner { login }
  labels(first: 100) { nodes { name } }
  assignees(first: 20) { nodes { login } }
  latestOpinionatedReviews(first: 100, writersOnly: true) {
    nodes { state author { login } }
  }
  timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT], last: 1) {
    nodes { ... on ReadyForReviewEvent { createdAt } }
  }
  commits(last: 1) {
    nodes {
      commit {
        statusCheckRollup {
          contexts(first: 100) {
            nodes { ... on CheckRun { name status conclusion externalId startedAt } }
          }
        }
      }
    }
  }
  files(first: 100) @include(if: $withFiles) {
    pageInfo { hasNextPage endCursor }
    nodes { path }
  }
}
"""

PR_QUERY = (
    """
query($owner: String!, $name: String!, $number: Int!, $withFiles: Boolean!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) { ...pr }
  }
}
"""
    + PR_FRAGMENT
)

HEAD_QUERY = (
    """
query($owner: String!, $name: String!, $branch: String!, $withFiles: Boolean!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, headRefName: $branch, first: 20) { nodes { ...pr } }
  }
}
"""
    + PR_FRAGMENT
)

SEARCH_QUERY = (
    """
query($search: String!, $cursor: String, $withFiles: Boolean!) {
  search(query: $search, type: ISSUE, first: 50, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ...pr }
  }
}
"""
    + PR_FRAGMENT
)

FILES_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      files(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { path }
      }
    }
  }
}
"""

# GitHub search returns at most this many results for one query.
SEARCH_RESULT_LIMIT = 1000


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("-p", "--pull-request", type=int, help="The PR number")
    target.add_argument(
        "--head",
        help="Head of the PR as <owner>:<branch>, used together with --sha",
    )
    target.add_argument("--all", action="store_true", help="Evaluate all open non-draft PRs")
    parser.add_argument("--sha", help="Head commit the PR must point at, used with --head")
    parser.add_argument("-o", "--org", default="zephyrproject-rtos", help="Github organization")
    parser.add_argument("-r", "--repo", default="zephyr", help="Github repository")
    parser.add_argument(
        "-M",
        "--maintainer-file",
        help="Maintainers file; enables the system wide change and area approvals criteria",
    )
    parser.add_argument(
        "--system-wide-areas",
        type=int,
        default=SYSTEM_WIDE_AREAS,
        help="Changed areas above which a PR is a system wide change "
        f"(default {SYSTEM_WIDE_AREAS})",
    )
    parser.add_argument(
        "--system-wide-review-days",
        type=int,
        default=SYSTEM_WIDE_REVIEW_BUSINESS_DAYS,
        help="Review period of a system wide change in business days "
        f"(default {SYSTEM_WIDE_REVIEW_BUSINESS_DAYS})",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Print the check run and labels instead of writing them",
    )
    parser.add_argument(
        "--now",
        type=datetime.datetime.fromisoformat,
        help="Evaluate at this ISO 8601 time instead of the current time",
    )

    args = parser.parse_args(argv)
    if args.head is not None and args.sha is None:
        parser.error("--head requires --sha")

    return args


def parse_time(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_time(value):
    return value.strftime("%Y-%m-%d %H:%M UTC")


def add_business_days(start, days):
    """Return the time at which *days* full days have passed since *start*,
    not counting Saturdays and Sundays (UTC)."""
    remaining = datetime.timedelta(days=days)
    t = start

    while remaining > datetime.timedelta(0):
        midnight = (t + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if t.weekday() >= 5:
            t = midnight
            continue

        step = min(remaining, midnight - t)
        t += step
        remaining -= step

    return t


def label_names(pr):
    return [node["name"] for node in pr["labels"]["nodes"]]


def author_login(pr):
    return pr["author"]["login"] if pr["author"] is not None else None


def review_states(pr):
    return {
        node["author"]["login"]: node["state"]
        for node in pr["latestOpinionatedReviews"]["nodes"]
        if node["author"] is not None
    }


@dataclasses.dataclass
class Result:
    """Outcome of one criterion for a pull request."""

    name: str
    met: bool
    # Lines describing the state and what is still needed.
    details: list
    # Short description of what is missing, used in the check run title.
    missing: str = ""
    # Check run annotations pointing at the files concerned.
    annotations: list = dataclasses.field(default_factory=list)


def blocking_labels(pr, now):
    blocking = [label for label in label_names(pr) if label in BLOCKING_LABELS]
    if len(blocking) > 0:
        return Result(
            "Blocking labels",
            False,
            [f"Labeled {', '.join(blocking)}, which must be removed"],
            f"blocked by label {', '.join(blocking)}",
        )

    return Result("Blocking labels", True, [f"None of {', '.join(BLOCKING_LABELS)}"])


def approvals(pr, now):
    assignees = [node["login"] for node in pr["assignees"]["nodes"]]
    states = review_states(pr)
    approved = [user for user, state in states.items() if state == "APPROVED"]
    author = author_login(pr)

    details = [f"Approved by {', '.join(approved)}" if len(approved) > 0 else "No approvals"]
    missing = []

    blocking = [a for a in assignees if states.get(a) == "CHANGES_REQUESTED"]
    if len(blocking) > 0:
        missing.append(f"changes requested by assignee {', '.join(blocking)}")
        details.append(
            f"Changes requested by assignee {', '.join(blocking)}, "
            "needs their approval or the review dismissed"
        )

    if len(approved) < MIN_APPROVALS:
        needed = MIN_APPROVALS - len(approved)
        missing.append(f"{len(approved)} of {MIN_APPROVALS} approvals")
        details.append(
            f"Needs {needed} more approval{'s' if needed > 1 else ''} by users with write access"
        )

    if len(assignees) == 0:
        details.append("No assignee")
    elif author in assignees:
        details.append(f"Author {author} is an assignee")
    elif not any(a in approved for a in assignees):
        missing.append("no assignee approval")
        details.append(f"Needs an approval by an assignee: {', '.join(assignees)}")

    return Result("Approvals", len(missing) == 0, details, ", ".join(missing))


def review_period(pr, now, system_wide_days=SYSTEM_WIDE_REVIEW_BUSINESS_DAYS):
    labels = label_names(pr)
    if HOTFIX_LABEL in labels:
        return Result("Review period", True, [f"No review period with the {HOTFIX_LABEL} label"])

    ready = pr["timelineItems"]["nodes"]
    if len(ready) > 0:
        start = parse_time(ready[-1]["createdAt"])
        since = "marked ready for review"
    else:
        start = parse_time(pr["createdAt"])
        since = "created"

    if SYSTEM_WIDE_LABEL in labels:
        end = add_business_days(start, system_wide_days)
        period = f"{system_wide_days} business days with the {SYSTEM_WIDE_LABEL} label"
    elif TRIVIAL_LABEL in labels:
        end = start + TRIVIAL_REVIEW_PERIOD
        hours = int(TRIVIAL_REVIEW_PERIOD.total_seconds() // 3600)
        period = f"{hours} hours with the {TRIVIAL_LABEL} label"
    else:
        end = add_business_days(start, REVIEW_PERIOD_BUSINESS_DAYS)
        period = f"{REVIEW_PERIOD_BUSINESS_DAYS} business days"

    details = [f"{period}, since the pull request was {since} at {format_time(start)}"]
    if now < end:
        details.append(f"Ends {format_time(end)}")
        return Result("Review period", False, details, f"review period ends {format_time(end)}")

    details.append(f"Ended {format_time(end)}")
    return Result("Review period", True, details)


def system_wide_change(pr, now, areas=SYSTEM_WIDE_AREAS):
    count = len(pr["areas"])
    changed = f"{count} area{'s' if count != 1 else ''} changed"
    details = [f"{changed}, a system wide change above {areas}"]
    if SYSTEM_WIDE_LABEL in label_names(pr):
        details.append(f"Labeled {SYSTEM_WIDE_LABEL}, which extends the review period")

    return Result("System wide change", True, details)


def area_approvals(pr, now):
    assignees = {node["login"].lower() for node in pr["assignees"]["nodes"]}
    approved = {user.lower() for user, state in review_states(pr).items() if state == "APPROVED"}
    author = author_login(pr)
    author = author.lower() if author is not None else None

    details = []
    missing = []
    annotations = []
    for name, (area, paths) in pr["areas"].items():
        if not area.requires_maintainer_approval:
            continue

        maintainers = area.maintainers
        assigned = [m for m in maintainers if m.lower() in assignees]
        approving = [m for m in maintainers if m.lower() in approved]
        if author in {m.lower() for m in maintainers}:
            details.append(f"{name}: the author is a maintainer")
        elif len(assigned) > 0:
            if len(assigned) > 1:
                details.append(f"{name}: maintainers {', '.join(assigned)} are assignees")
            else:
                details.append(f"{name}: maintainer {assigned[0]} is an assignee")
        elif len(approving) > 0:
            details.append(f"{name}: approved by maintainer {', '.join(approving)}")
        else:
            missing.append(name)
            listed = ", ".join(maintainers) if len(maintainers) > 0 else "none listed"
            details.append(f"{name}: needs an approval by a maintainer ({listed})")
            annotations.extend(
                {
                    "path": path,
                    "start_line": 1,
                    "end_line": 1,
                    "annotation_level": "warning",
                    "title": f"{name} maintainer approval required",
                    "message": f"Changes to the {name} area need the approval of one of "
                    f"its maintainers: {listed}",
                }
                for path in paths
            )

    if len(details) == 0:
        details.append("No changed area requires maintainer approval")

    return Result(
        "Area approvals",
        len(missing) == 0,
        details,
        f"waiting for approval by a maintainer of {', '.join(missing)}",
        annotations,
    )


# Each criterion returns a Result for a pull request at a given time.
CRITERIA = [blocking_labels, approvals, review_period]


@dataclasses.dataclass
class Evaluation:
    """Check run output for a pull request."""

    met: bool
    title: str
    summary: str
    text: str
    annotations: list

    @property
    def fingerprint(self):
        output = [self.met, self.title, self.summary, self.text, self.annotations]
        return hashlib.sha256(json.dumps(output, sort_keys=True).encode()).hexdigest()

    @property
    def check(self):
        """The (status, conclusion, external_id) the check run should have."""
        if self.met:
            return "completed", "success", self.fingerprint
        return "in_progress", None, self.fingerprint


def evaluate(pr, now, criteria=CRITERIA):
    results = [criterion(pr, now) for criterion in criteria]
    unmet = [result for result in results if not result.met]

    if len(unmet) > 0:
        title = "Pending: " + "; ".join(result.missing for result in unmet)
    else:
        title = "All merge criteria met"

    rows = ["| Criterion | State | Details |", "| --- | --- | --- |"]
    for result in results:
        state = "Met" if result.met else "Pending"
        rows.append(f"| {result.name} | {state} | {'<br>'.join(result.details)} |")

    text = ""
    if "areas" in pr:
        lines = [f"- {name}: {len(paths)} files" for name, (_, paths) in pr["areas"].items()]
        text = "Changed areas:\n\n" + "\n".join(lines) if len(lines) > 0 else "No changed areas"

    return Evaluation(
        len(unmet) == 0,
        title[:OUTPUT_TITLE_LIMIT],
        "\n".join(rows)[:OUTPUT_TEXT_LIMIT],
        text[:OUTPUT_TEXT_LIMIT],
        [annotation for result in results for annotation in result.annotations],
    )


def current_check(pr):
    """Return (status, conclusion, external_id) of the latest check run."""
    runs = [
        context
        for commit in pr["commits"]["nodes"]
        if commit["commit"]["statusCheckRollup"] is not None
        for context in commit["commit"]["statusCheckRollup"]["contexts"]["nodes"]
        if context.get("name") == CHECK_NAME
    ]
    if len(runs) == 0:
        return None

    run = max(runs, key=lambda run: run["startedAt"] or "")
    conclusion = run["conclusion"].lower() if run["conclusion"] is not None else None
    return run["status"].lower(), conclusion, run["externalId"]


class ChangedAreas:
    """Maps changed files to the areas of a maintainers file."""

    def __init__(self, filename):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from get_maintainer import Maintainers

        self.maintainers = Maintainers(filename)
        self.toplevel = self.maintainers.filename.parent

    def __call__(self, paths):
        """Return a dict mapping area names to (Area, changed paths)."""
        changed = {}
        for path in paths:
            # path2areas() takes paths relative to the current directory.
            areas = self.maintainers.path2areas(os.path.relpath(self.toplevel / path))

            # As in scripts/ci/set_assignees.py, an area covering the file only
            # through a deferring file group yields to the other areas.
            non_deferred = [area for area in areas if not area.is_deferred_for_path(path)]
            if len(non_deferred) > 0:
                areas = non_deferred

            for area in areas:
                if not area.meta:
                    changed.setdefault(area.name, (area, []))[1].append(path)

        return {name: changed[name] for name in sorted(changed)}


class MergeCriteria:
    def __init__(self, args):
        self.args = args
        self.now = args.now if args.now is not None else datetime.datetime.now(datetime.UTC)
        if self.now.tzinfo is None:
            self.now = self.now.replace(tzinfo=datetime.UTC)

        self.criteria = [
            blocking_labels,
            approvals,
            functools.partial(review_period, system_wide_days=args.system_wide_review_days),
        ]

        self.changed_areas = None
        if args.maintainer_file is not None:
            self.changed_areas = ChangedAreas(args.maintainer_file)
            self.criteria.append(
                functools.partial(system_wide_change, areas=args.system_wide_areas)
            )
            self.criteria.append(area_approvals)

        auth = github.Auth.Token(os.environ.get('GITHUB_TOKEN', None))
        self.gh = github.Github(auth=auth)

    def query(self, query, **variables):
        _, data = self.gh.requester.graphql_query(query, variables)
        return data["data"]

    def query_pr(self, query, **variables):
        with_files = self.changed_areas is not None
        return self.query(query, withFiles=with_files, **variables)

    def api(self, method, path, body):
        _, data = self.gh.requester.requestJsonAndCheck(
            method, f"/repos/{self.args.org}/{self.args.repo}/{path}", input=body
        )
        return data

    def get_pull_request(self, number):
        data = self.query_pr(PR_QUERY, owner=self.args.org, name=self.args.repo, number=number)
        return data["repository"]["pullRequest"]

    def find_pull_request(self, head, sha):
        owner, branch = head.split(":", 1)
        data = self.query_pr(HEAD_QUERY, owner=self.args.org, name=self.args.repo, branch=branch)
        for pr in data["repository"]["pullRequests"]["nodes"]:
            if pr["headRepositoryOwner"]["login"] == owner and pr["headRefOid"] == sha:
                return pr

        return None

    def all_pull_requests(self):
        # Search results are capped, so walk the open pull requests in
        # windows ordered by creation time, each starting where the previous
        # one was cut off.
        base = f"repo:{self.args.org}/{self.args.repo} is:pr is:open draft:false sort:created-asc"
        seen = set()
        since = None

        while True:
            search = base if since is None else f"{base} created:>={since}"
            cursor = None
            new = 0

            while True:
                page = self.query_pr(SEARCH_QUERY, search=search, cursor=cursor)["search"]
                for pr in page["nodes"]:
                    since = pr["createdAt"]
                    if pr["number"] in seen:
                        continue
                    seen.add(pr["number"])
                    new += 1
                    yield pr

                if not page["pageInfo"]["hasNextPage"]:
                    break
                cursor = page["pageInfo"]["endCursor"]

            if page["issueCount"] <= SEARCH_RESULT_LIMIT or new == 0:
                return

    def prepare(self, pr):
        """Map the changed files of *pr* to areas and label a system wide
        change, when a maintainers file is used."""
        if self.changed_areas is None:
            return

        files = pr["files"]
        while files["pageInfo"]["hasNextPage"]:
            data = self.query(
                FILES_QUERY,
                owner=self.args.org,
                name=self.args.repo,
                number=pr["number"],
                cursor=files["pageInfo"]["endCursor"],
            )
            page = data["repository"]["pullRequest"]["files"]
            files["nodes"].extend(page["nodes"])
            files["pageInfo"] = page["pageInfo"]

        pr["areas"] = self.changed_areas([node["path"] for node in files["nodes"]])

        if len(pr["areas"]) <= self.args.system_wide_areas or SYSTEM_WIDE_LABEL in label_names(pr):
            return

        print(f"pr: {pr['url']} changes {len(pr['areas'])} areas, adding {SYSTEM_WIDE_LABEL}")
        if not self.args.dry_run:
            self.api("POST", f"issues/{pr['number']}/labels", {"labels": [SYSTEM_WIDE_LABEL]})
        pr["labels"]["nodes"].append({"name": SYSTEM_WIDE_LABEL})

    def write_check(self, pr, evaluation):
        status, conclusion, external_id = evaluation.check
        now = datetime.datetime.now(datetime.UTC).isoformat()
        annotations = evaluation.annotations

        def output(batch):
            result = {
                "title": evaluation.title,
                "summary": evaluation.summary,
                "annotations": batch,
            }
            if evaluation.text != "":
                result["text"] = evaluation.text
            return result

        body = {
            "name": CHECK_NAME,
            "head_sha": pr["headRefOid"],
            "external_id": external_id,
            "status": status,
            "started_at": now,
            "output": output(annotations[:ANNOTATIONS_PER_REQUEST]),
        }
        if conclusion is not None:
            body["conclusion"] = conclusion
            body["completed_at"] = now
        if "GITHUB_RUN_ID" in os.environ:
            body["details_url"] = (
                f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
                f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            )

        run = self.api("POST", "check-runs", body)

        # Annotations beyond the per-request limit are added to the check run.
        for i in range(ANNOTATIONS_PER_REQUEST, len(annotations), ANNOTATIONS_PER_REQUEST):
            batch = annotations[i : i + ANNOTATIONS_PER_REQUEST]
            self.api("PATCH", f"check-runs/{run['id']}", {"output": output(batch)})

    def update(self, pr):
        if pr["isDraft"]:
            print(f"pr: {pr['url']} is a draft, skipping")
            return False

        self.prepare(pr)
        evaluation = evaluate(pr, self.now, self.criteria)
        print(f"pr: {pr['url']} at {pr['headRefOid']}: {evaluation.title}")
        print(evaluation.summary)
        for annotation in evaluation.annotations:
            print(f"annotation: {annotation['path']}: {annotation['title']}")

        if current_check(pr) == evaluation.check:
            print("check run unchanged")
            return False

        if not self.args.dry_run:
            self.write_check(pr, evaluation)
        return True

    def update_all(self):
        total = 0
        changed = 0
        for pr in self.all_pull_requests():
            # The search index can lag behind a conversion to draft.
            if pr["isDraft"]:
                continue

            total += 1
            self.prepare(pr)
            check = current_check(pr)
            evaluation = evaluate(pr, self.now, self.criteria)
            if check == evaluation.check:
                continue

            # A required check that was never reported blocks merging like an
            # unfinished one does; only report it once the criteria are met.
            if check is None and not evaluation.met:
                continue

            # Evaluate a fresh copy so that a review or push since the page
            # was fetched is not overwritten with a stale result.
            if self.update(self.get_pull_request(pr["number"])):
                changed += 1

        print(f"{total} open non-draft pull requests, {changed} check runs changed")


def main(argv):
    args = parse_args(argv)
    criteria = MergeCriteria(args)

    if args.all:
        criteria.update_all()
        return 0

    if args.pull_request is not None:
        pr = criteria.get_pull_request(args.pull_request)
    else:
        pr = criteria.find_pull_request(args.head, args.sha)
        if pr is None:
            print(f"No open pull request for {args.head} at {args.sha}, nothing to do.")
            return 0

    criteria.update(pr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
