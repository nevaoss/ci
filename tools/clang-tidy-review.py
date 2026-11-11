#!/usr/bin/env python3
"""Posts the result of the clang-tidy job on its PR.

Unlike the other scripts in this directory, this one does not trigger a
Jenkins job. It runs after tools/clang-tidy.py, on the result it printed.

Usage: clang-tidy-review.py RESULT

  RESULT  results.json of the Jenkins clang-tidy job

Environment:

  GH_TOKEN            token with pull-requests: write
  GITHUB_REPOSITORY   owner/name of the repository
  GITHUB_PR_NUMBER    number of the PR
  GITHUB_PR_HEAD_SHA  commit the result is for
  CLANG_TIDY_HEADER   first line of the review or comment, such as
                      "clang-tidy found 2 issues:"

The diagnostics in the clang-tidy output of "review" are posted in one
review, each on its line. GitHub rejects a whole review if one comment is
on a line outside the diff, so those diagnostics are listed in the review
body instead.

Re-runs do not pile up threads. A diagnostic is identified by its first
line, such as "warning: use nullptr [modernize-use-nullptr]", and where it
is, leaving out source excerpts and notes, which can change while the
warning does not. Hidden markers carry a hash of it, and on each run:
- a diagnostic that already has a thread at its current line, resolved or
  not, is not posted again,
- a diagnostic outside the diff that an earlier review listed is not
  listed again,
- unresolved threads whose diagnostic is gone, or whose line is outdated,
  are resolved, and
- no review is posted when there is nothing new.

The result is posted as a single PR comment instead when the number of
diagnostics is unknown or zero, when the parsed count does not match it,
or when a request to read or post the review fails.
"""

import hashlib
import json
import os
import re
import subprocess
import sys


AUTHORS = ("github-actions", "github-actions[bot]")
LINE_MARKER = re.compile(r"^<!-- clang-tidy:([0-9a-f]+) -->")
OUTSIDE_MARKER = re.compile(r"<!-- clang-tidy-outside:([0-9a-f]+) -->")
BLOCK = re.compile(r"^```text\n(.*?)^```", re.MULTILINE | re.DOTALL)
DIAGNOSTIC = re.compile(
  r"^(?P<path>[^\s:][^:]*):(?P<line>\d+):\d+: "
  r"(?P<message>(?:warning|error): .*)$")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved path line
          comments(first: 1) { nodes { body author { login } } }
        }
      }
    }
  }
}
"""

RESOLVE_MUTATION = """
mutation($id: ID!) {
  resolveReviewThread(input: {threadId: $id}) { thread { id } }
}
"""


class ParseError(Exception):
  pass


def gh(*args, input=None):
  return subprocess.run(["gh", *args], input=input, capture_output=True,
                        text=True, check=True).stdout


def gh_lines(*args):
  """Runs gh with a --jq filter that prints one JSON value per line."""
  return [json.loads(line) for line in gh(*args).splitlines() if line]


def digest(*parts):
  text = "\n".join(map(str, parts))
  return hashlib.sha256(text.encode()).hexdigest()[:16]


def parse_diagnostics(review, expected):
  """Returns the diagnostics in the clang-tidy output of a review.

  Each diagnostic starts with "path:line:col: warning: ...", and the lines
  up to the next one (source, caret, fix-it, notes) belong to it. Paths are
  relative to the build directory, such as "../../neva/foo.cc".
  """
  diagnostics = []
  block = BLOCK.search(review or "")
  for text in (block.group(1) if block else "").splitlines():
    match = DIAGNOSTIC.match(text)
    if match:
      path = match.group("path")
      while path.startswith("../"):
        path = path[len("../"):]
      diagnostics.append({"path": path,
                          "line": int(match.group("line")),
                          "text": [match.group("message")]})
    elif diagnostics:
      diagnostics[-1]["text"].append(text)

  if len(diagnostics) != expected:
    raise ParseError(f"Parsed {len(diagnostics)} clang-tidy diagnostics, "
                     f"expected {expected}")
  return diagnostics


def commentable_lines(patch):
  """Returns the new-side lines of a patch that can be commented on."""
  lines = set()
  line = 0
  for text in (patch or "").splitlines():
    match = HUNK.match(text)
    if match:
      line = int(match.group(1))
    elif text.startswith(("-", "\\")):
      continue
    else:
      lines.add(line)
      line += 1
  return lines


def plan(diagnostics, files, threads, reviews, header, head_sha):
  """Returns the review to post, or None, and the threads to resolve.

  files maps each PR file to its commentable lines. threads and reviews
  are the review threads and reviews of the PR, from earlier runs or not.
  """
  # Threads of earlier runs, keyed by their path, their current line (None
  # once outdated, as GitHub cannot map it any more) and the diagnostic
  # hash. Resolved threads count too, so a diagnostic that was resolved
  # without a fix is not posted again.
  earlier = {}
  for thread in threads:
    match = LINE_MARKER.match(thread["body"] or "")
    if thread["author"] in AUTHORS and match:
      key = (thread["path"], thread["line"], match.group(1))
      earlier.setdefault(key, []).append(thread)

  # Diagnostics outside the diff listed by earlier runs.
  listed = set()
  for review in reviews:
    if review["author"] in AUTHORS:
      listed.update(OUTSIDE_MARKER.findall(review["body"] or ""))

  reported = set()
  comments = []
  outside = []
  for d in diagnostics:
    snippet = "```text\n" + "\n".join(d["text"]).rstrip() + "\n```"
    if d["line"] in files.get(d["path"], ()):
      key = (d["path"], d["line"], digest(d["text"][0]))
      if key not in earlier and key not in reported:
        comments.append({"path": d["path"], "line": d["line"],
                         "side": "RIGHT",
                         "body": f"<!-- clang-tidy:{key[2]} -->\n{snippet}"})
      reported.add(key)
    else:
      key = digest(d["path"], d["line"], d["text"][0])
      if key not in listed:
        outside.append(f"<!-- clang-tidy-outside:{key} -->\n"
                       f"`{d['path']}:{d['line']}`\n{snippet}")
      listed.add(key)

  resolve = [t["id"] for key, same in earlier.items() if key not in reported
             for t in same if not t["isResolved"]]

  # Nothing is posted when every diagnostic is already reported, or when
  # there are none.
  if not comments and not outside:
    return None, resolve

  body = header
  if outside:
    body += "\n\nOutside the diff:\n\n" + "\n\n".join(outside)
  return {"commit_id": head_sha, "event": "COMMENT", "body": body,
          "comments": comments}, resolve


def post_review(repo, number, head_sha, header, result):
  diagnostics = parse_diagnostics(result.get("review"),
                                  int(result["diagnostics"]))

  files = {f["filename"]: commentable_lines(f["patch"]) for f in gh_lines(
    "api", "--paginate", f"repos/{repo}/pulls/{number}/files",
    "--jq", ".[] | {filename, patch}")}
  reviews = gh_lines(
    "api", "--paginate", f"repos/{repo}/pulls/{number}/reviews",
    "--jq", ".[] | {author: .user.login, body}")
  owner, name = repo.split("/")
  threads = gh_lines(
    "api", "graphql", "--paginate",
    "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={number}",
    "-f", f"query={THREADS_QUERY}",
    "--jq", ".data.repository.pullRequest.reviewThreads.nodes[]"
            " | {id, isResolved, path, line,"
            " body: .comments.nodes[0].body,"
            " author: .comments.nodes[0].author.login}")

  review, resolve = plan(diagnostics, files, threads, reviews, header,
                         head_sha)
  if review:
    gh("api", "--method", "POST", f"repos/{repo}/pulls/{number}/reviews",
       "--input", "-", input=json.dumps(review))
    print(f"Posted {len(review['comments'])} review comments.")
  else:
    print("No new diagnostics to post.")

  # The review is already posted, so a failure here only leaves a thread
  # open.
  for thread in resolve:
    try:
      gh("api", "graphql", "-f", f"id={thread}",
         "-f", f"query={RESOLVE_MUTATION}")
    except subprocess.CalledProcessError as e:
      print(f"::warning::Failed to resolve review thread {thread}: "
            f"{e.stderr.strip()}")
  if resolve:
    print(f"Resolved {len(resolve)} review threads.")


def post_comment(repo, number, header, result):
  review = result.get("review")
  if review:
    # The review is Markdown with its own code block; wrapping it in
    # another one would break the rendering.
    body = f"{header}\n\n{review}\n"
  else:
    body = "clang-tidy check completed successfully. No issues found.\n"
  gh("pr", "comment", number, "--repo", repo, "--body-file", "-",
     input=body)
  print("Posted the result as a PR comment.")


def main():
  if len(sys.argv) != 2:
    print(f"Usage: {sys.argv[0]} RESULT", file=sys.stderr)
    return 2

  repo = os.environ["GITHUB_REPOSITORY"]
  number = os.environ["GITHUB_PR_NUMBER"]
  head_sha = os.environ["GITHUB_PR_HEAD_SHA"]
  header = os.environ["CLANG_TIDY_HEADER"]

  with open(sys.argv[1]) as f:
    result = json.load(f)

  current = gh("api", f"repos/{repo}/pulls/{number}", "--jq", ".head.sha")
  if current.strip() != head_sha:
    print("Pull Request was updated. Obsolete result will not be posted.")
    return 0

  # Without a known number the diagnostics cannot be checked, so the result
  # goes in a single PR comment. A clean result gets one too, which needs
  # no resolution.
  if result.get("diagnostics") is not None:
    try:
      post_review(repo, number, head_sha, header, result)
    except (ParseError, subprocess.CalledProcessError, KeyError,
            ValueError) as e:
      detail = getattr(e, "stderr", None) or e
      print(f"::warning::Could not post the review: {str(detail).strip()}. "
            "Posting the result as a single PR comment.")
    else:
      if int(result["diagnostics"]) != 0:
        return 0

  post_comment(repo, number, header, result)
  return 0


if __name__ == "__main__":
  sys.exit(main())
