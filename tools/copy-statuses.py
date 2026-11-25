#!/usr/bin/env python3
"""Copy build and test statuses from one PR head to another with the same tree.

A rebase merge gives commits new SHAs but keeps their tree (file contents).
Statuses belong to a SHA, so an upgrade PR into neva-main-rolling has no
results even though the same code was verified on its adaptation PR. Run by
hand with both PR numbers, this script copies the results of the source PR
head to the target PR head when the two heads have the same tree.

Rules:
- The trees of the two heads must be the same; otherwise nothing is copied.
- Only the build and test contexts in CONTEXTS are copied. jenkins/clang-tidy
  analyzes the PR diff, not the tree, so it is not copied.
- For each context, only the newest status posted by github-actions[bot] on
  the source head is used, and it is copied only if it is a success. A later
  failed re-run is therefore not covered by an earlier success.
- A context that the target head already has, in any state, is kept.
- The copied status keeps the original link and notes the source in its
  description, e.g. "Jenkins PC build #1194 passed (verified on dafeacb,
  #248)".

Environment: GITHUB_REPOSITORY, GH_TOKEN, SOURCE_PR, TARGET_PR.
"""

import json
import os
import sys
import urllib.error
import urllib.request

API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
CONTEXTS = (
    "jenkins/build-pc",
    "jenkins/build-webos",
    "jenkins/build-webos-qemux86",
    "jenkins/test-pc-bvt",
    "jenkins/test-pc-frt",
    "jenkins/test-webos-bvt",
    "jenkins/test-webos-frt",
)
TRUSTED_CREATOR = "github-actions[bot]"
MAX_DESCRIPTION = 140


class Stop(Exception):
  """Nothing can be copied; the message says why."""


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
  Filtering by creator first means a later status posted by someone else
  cannot hide a failure posted by our workflows.
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


def copy_statuses(repo, source_pr, target_pr):
  source = request("GET", f"/repos/{repo}/pulls/{source_pr}")
  target = request("GET", f"/repos/{repo}/pulls/{target_pr}")
  if (target["head"].get("repo") or {}).get("full_name") != repo:
    raise Stop(f"Target #{target_pr} comes from a fork; statuses cannot be "
               "posted on it.")

  source_sha = source["head"]["sha"]
  target_sha = target["head"]["sha"]
  source_tree = tree_of(repo, source_sha)
  target_tree = tree_of(repo, target_sha)
  print(f"Source #{source_pr}: head {source_sha[:7]}, tree {source_tree[:7]}")
  print(f"Target #{target_pr}: head {target_sha[:7]}, tree {target_tree[:7]}")
  if source_tree != target_tree:
    raise Stop("Tree mismatch; nothing copied.")

  source_statuses = latest_statuses(repo, source_sha, TRUSTED_CREATOR)
  target_statuses = latest_statuses(repo, target_sha)

  copied = kept = 0
  for context in CONTEXTS:
    status = source_statuses.get(context)
    if status is None:
      print(f"  {context}: no result on the source head")
      continue
    if status["state"] != "success":
      print(f"  {context}: newest result on the source head is "
            f"{status['state']}; not copied")
      continue
    if context in target_statuses:
      print(f"  {context}: kept the existing "
            f"{target_statuses[context]['state']} status on the target head")
      kept += 1
      continue

    suffix = f"(verified on {source_sha[:7]}, #{source_pr})"
    description = (status.get("description") or "").strip()
    description = description[:MAX_DESCRIPTION - len(suffix) - 1]
    description = f"{description} {suffix}".strip()
    request("POST", f"/repos/{repo}/statuses/{target_sha}", {
        "state": "success",
        "context": context,
        "description": description,
        "target_url": status["target_url"],
    })
    print(f"  {context}: {description}")
    copied += 1

  # A re-run after a successful copy keeps everything, which is fine.
  if not copied and not kept:
    raise Stop("No successful build or test result on the source head; "
               "nothing copied.")
  print(f"Copied {copied} statuses, kept {kept}.")


def main():
  try:
    copy_statuses(os.environ["GITHUB_REPOSITORY"],
                  int(os.environ["SOURCE_PR"]),
                  int(os.environ["TARGET_PR"]))
  except Stop as e:
    print(f"::error::{e}")
    sys.exit(1)
  except urllib.error.HTTPError as e:
    print(f"::error::GitHub API {e.code} for {e.url}: {e.read().decode()}")
    sys.exit(1)


if __name__ == "__main__":
  main()
