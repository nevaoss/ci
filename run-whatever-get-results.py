#!/usr/bin/env python3
import json
import os
import sys

from relay import Job, log_print

def error(description):
  log_print(description)
  print(json.dumps({
    "status": "error",
    "description": description
  }))
  sys.exit(-1)

try:
  jobname, _ = os.path.relpath(sys.argv[0], ".").split(os.path.extsep)
except ValueError:
  error("Invalid job name")

try:
  q = Job(f"NEVA/{jobname}").trigger()
  log_print(f"Started {q.build.name}.")
except BaseException as e:
  error(f"Job {jobname} trigger failed")

try:
  status = q.join()
except Exception as e:
  error(f"Job {jobname} status request failed")

if status != "SUCCESS":
  error(f"Job completed with status {status}")

try:
  results = q.get_artifact("results.json")
except FileNotFoundError as e:
  error("Artifact results.json is not found")
except Exception as e:
  error("Artifact results.json request failed")

try:
  json.loads(results)
except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as e:
  error("Artifact results.json is invalid")

print(results)
sys.exit(0)