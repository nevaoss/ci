#!/usr/bin/env python3
import os
import sys

from relay import Job

jobname, _ = os.path.basename(sys.argv[0]).split(os.path.extsep)

q = Job(f"NEVA/{jobname}").trigger()
print(f"Started {q.build.name}...")

status = q.join()
print(f"...completed with {status}")
if status != "SUCCESS":
  sys.exit(-1)

sys.exit(0)
