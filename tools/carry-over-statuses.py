#!/usr/bin/env python3
"""Carry over jenkins/* commit statuses to a PR head that has the same tree.

A rebase merge gives commits new SHAs but keeps their tree (file contents).
Statuses belong to a SHA, so a PR whose code was already verified under
another SHA would have to run every Jenkins job again. This script finds an
already verified commit with the same tree as the PR head and copies its
successful jenkins/* statuses to the head.

GitHub cannot search commits by tree, so the candidates are:
  1. the last head of each PR merged into this PR's head branch,
  2. previous heads of this PR (force pushes).

Only build and test results are carried over, since they depend on the tree
alone; jenkins/clang-tidy analyzes the PR diff and is left out. Only
statuses posted by github-actions[bot] count. For each context, the newest
of these statuses among all candidates with the same tree is used, and it is
copied only if it is a success and the PR head does not have that context
yet. PRs from forks are skipped (their token cannot post statuses).

Environment: GITHUB_REPOSITORY, GH_TOKEN, PR_NUMBER.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
CONTEXT_PREFIX = "jenkins/"
# Results that depend on the PR diff, not only on the tree.
EXCLUDED_CONTEXTS = {"jenkins/clang-tidy"}
TRUSTED_CREATOR = "github-actions[bot]"
MAX_DESCRIPTION = 140
MAX_CANDIDATES = 30

FORCE_PUSHES_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      timelineItems(last: 30, itemTypes: [HEAD_REF_FORCE_PUSHED_EVENT]) {
        nodes { ... on HeadRefForcePushedEvent { beforeCommit { oid } } }
      }
    }
  }
}
"""


def request(method, path, body=None):
  data = json.dumps(body).encode() if body is not None else None
  req = urllib.request.Request(
      f"{API_URL}{path}", data=data, method=method,
      headers={
          "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
          "Accept": "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
      })
  with urllib.request.urlopen(req) as response:
    return json.load(response)


def tree_of(repo, sha):
  return request("GET", f"/repos/{repo}/git/commits/{sha}")["tree"]["sha"]


def latest_statuses(repo, sha, creator=None):
  """Return the newest status per context, only from creator if given.

  The API lists every status update, newest first, so all pages are read.
  """
  statuses = {}
  page = 1
  while True:
    batch = request("GET", f"/repos/{repo}/commits/{sha}/statuses"
                           f"?per_page=100&page={page}")
    for status in batch:
      if creator and (status.get("creator") or {}).get("login") != creator:
        continue
      statuses.setdefault(status["context"], status)
    if len(batch) < 100:
      return statuses
    page += 1


def merged_pr_heads(repo, branch):
  """Yield (sha, label) for PRs merged into branch, most recent first."""
  query = urllib.parse.urlencode({
      "state": "closed", "base": branch, "sort": "updated",
      "direction": "desc", "per_page": MAX_CANDIDATES})
  pulls = [p for p in request("GET", f"/repos/{repo}/pulls?{query}")
           if p.get("merged_at")]
  pulls.sort(key=lambda p: p["merged_at"], reverse=True)
  for pull in pulls:
    yield pull["head"]["sha"], f"#{pull['number']}"


def previous_heads(repo, number):
  """Yield (sha, label) for heads replaced by force pushes, newest first."""
  owner, name = repo.split("/")
  result = request("POST", "/graphql", {
      "query": FORCE_PUSHES_QUERY,
      "variables": {"owner": owner, "name": name, "number": number}})
  nodes = (result["data"]["repository"]["pullRequest"]
           ["timelineItems"]["nodes"])
  for node in reversed(nodes):
    if node.get("beforeCommit"):
      yield node["beforeCommit"]["oid"], "previous head"


def carry_over(repo, number):
  pull = request("GET", f"/repos/{repo}/pulls/{number}")
  if (pull["head"].get("repo") or {}).get("full_name") != repo:
    print(f"PR #{number} comes from a fork; skipped.")
    return
  head_sha = pull["head"]["sha"]
  head_tree = tree_of(repo, head_sha)
  present = latest_statuses(repo, head_sha)
  print(f"PR #{number}: head {head_sha[:7]}, tree {head_tree[:7]}")

  # For each context, the newest trusted status among all candidates with
  # the same tree: {context: (status, sha, label)}.
  newest = {}
  seen = {head_sha}
  candidates = [*merged_pr_heads(repo, pull["head"]["ref"]),
                *previous_heads(repo, number)]
  for sha, label in candidates:
    if sha in seen:
      continue
    seen.add(sha)

    if tree_of(repo, sha) != head_tree:
      continue
    print(f"{sha[:7]} ({label}) has the same tree")

    # Filter by creator before taking the newest status per context, so that
    # a later status posted by someone else cannot hide our failure.
    for context, status in latest_statuses(repo, sha, TRUSTED_CREATOR).items():
      if (context.startswith(CONTEXT_PREFIX)
          and context not in EXCLUDED_CONTEXTS
          and (context not in newest
               or status["created_at"] > newest[context][0]["created_at"])):
        newest[context] = (status, sha, label)

  if not newest:
    print("No verified commit with the same tree; nothing to carry over.")
    return

  for context, (status, sha, label) in sorted(newest.items()):
    if context in present:
      print(f"  {context}: kept the existing {present[context]['state']} "
            "status")
      continue
    if status["state"] != "success":
      print(f"  {context}: newest result is {status['state']} on {sha[:7]}; "
            "not carried over")
      continue
    suffix = f"(verified on {sha[:7]}, {label})"
    description = (status.get("description") or "").strip()
    description = description[:MAX_DESCRIPTION - len(suffix) - 1]
    description = f"{description} {suffix}".strip()
    request("POST", f"/repos/{repo}/statuses/{head_sha}", {
        "state": "success",
        "context": context,
        "description": description,
        "target_url": status["target_url"],
    })
    print(f"  {context}: {description}")


def main():
  try:
    carry_over(os.environ["GITHUB_REPOSITORY"], int(os.environ["PR_NUMBER"]))
  except urllib.error.HTTPError as e:
    print(f"::error::GitHub API {e.code} for {e.url}: {e.read().decode()}")
    sys.exit(1)


if __name__ == "__main__":
  main()
