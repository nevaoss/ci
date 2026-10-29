#!/usr/bin/env python3
import os
import sys

from relay import Job

try:
  jobname, _ = os.path.relpath(sys.argv[0], ".").split(os.path.extsep)
except ValueError:
  print("Invalid job name.")
  sys.exit(-1)

try:
  q = Job(f"NEVA/{jobname}").trigger()
  print(f"Started {q.build.name}.")
except Exception as e:
  print(f"Job {jobname} trigger failed: {e}")
  sys.exit(-1)

try:
  status = q.join()
except Exception as e:
  print(f"Job {jobname} status request failed: {e}")
  sys.exit(-1)

if status != "SUCCESS":
  print(f"Job completed with status {status}")
  sys.exit(-1)

print(f"Completed with {status}.")
sys.exit(0)
