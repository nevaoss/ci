#!/usr/bin/env python3

import json
import os
import signal
import sys

from relay import Job, log_print


current_queue = None
cancelling = False


def error(description):
  log_print(description)
  print(json.dumps({
    "status": "error",
    "description": description
  }))
  sys.exit(-1)


def cancel_jenkins_job(signum, frame):
  global cancelling

  if cancelling:
    log_print("Job has already been canceled")
    return

  cancelling = True
  signal_name = signal.Signals(signum).name
  log_print(f"Received {signal_name}")

  if current_queue is not None:
    try:
      current_queue.cancel()
    except Exception as e:
      log_print(f"Jenkins cancellation failed: {e}")

  sys.exit(128 + signum)


signal.signal(signal.SIGTERM, cancel_jenkins_job)
signal.signal(signal.SIGINT, cancel_jenkins_job)


try:
  jobname, _ = os.path.relpath(sys.argv[0], ".").split(os.path.extsep)
except ValueError:
  error("Invalid job name")

try:
  q = Job(f"NEVA/{jobname}").trigger()
  current_queue = q
  log_print(f"Started {q.build.name}.")
except Exception as e:
  error(f"Job {jobname} trigger failed")

try:
  status = q.join()
except Exception as e:
  error(f"Job {jobname} status request failed")
finally:
  current_queue = None

if status != "SUCCESS":
  error(f"Job completed with status {status}")

try:
  results = q.get_artifact("results.json")
except FileNotFoundError as e:
  error("Artifact results.json is not found")
except Exception as e:
  error("Artifact results.json request failed")

try:
  results = results.decode("utf-8")
  json.loads(results)
except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as e:
  error("Artifact results.json is invalid")

print(results)
sys.exit(0)