#!/usr/bin/env python3
import sys
import os
try:
  from jenkinsapi.jenkins import Jenkins
except ModuleNotFoundError:
  import pip, site
  pip.main("install --user jenkinsapi".split())
  usp = site.getusersitepackages()
  if usp not in sys.path:
    sys.path.append(usp)

from jenkinsapi.jenkins import Jenkins
from jenkinsapi.custom_exceptions import UnknownJob

JENKINS_URL = os.environ.get("JENKINS_URL")
CI_TOKEN = os.environ.get("CI_TOKEN")

def fatal(e):
  print(f"FATAL: {e}")
  sys.exit(-1)

if not JENKINS_URL or not CI_TOKEN:
  fatal("Neither JENKNIS_URL nor CI_TOKEN env variable is not specified")

class Job:
  def __init__(self, jobname):
    self.jobname = jobname

  def trigger(self):
    user, passwd = CI_TOKEN.split(":")
    try:
      j = Jenkins(JENKINS_URL, user, passwd)
    except Exception as e:
      fatal(e)

    try:
      job = j.get_job(self.jobname)
    except UnknownJob as e:
      fatal(e)

    q = job.invoke(
      build_params = {
        var: os.environ[var]
        for var in os.environ
        if var.startswith("GITHUB_")
    })

    q.block_until_building()
    return Job.Queue(q)

  class Queue:
    def __init__(self, q):
      self.q = q

    @property
    def build(self):
      return self.q.get_build()

    def join(self):
      if not self.q:
        return
      self.q.block_until_complete()
      return self.build.get_status()
