#!/usr/bin/env python3
import sys
import os
import time

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
from requests.exceptions import RequestException

JENKINS_URL = os.environ.get("JENKINS_URL")
CI_TOKEN = os.environ.get("CI_TOKEN")

# Only the actual result should be passed to stdout for further analysis.
# All logs should be written to the stderr
def log_print(message):
  print(message, file=sys.stderr, flush=True)

def fatal(e):
  log_print(f"FATAL: {e}")
  sys.exit(-1)

if not JENKINS_URL or not CI_TOKEN:
  fatal("Neither JENKINS_URL nor CI_TOKEN env variable is specified")

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
      self._build = None

    @property
    def build(self):
      if not self._build:
        self._build = self.q.get_build()

      return self._build

    def cancel(self):
      try:
        build = self.build
        log_print(f"Stopping Jenkins build {build.name}")
        build.stop()
        log_print(f"Stop requested for Jenkins build {build.name}")
      except Exception as e:
        log_print(f"Failed to stop Jenkins build: {e}")
        raise

    def retry(self, description, callback):
      for attempt in range(5):
        try:
          log_print(
            f"{description} "
            f"(attempt {attempt + 1}/5)"
          )
          return callback()
        except RequestException as e:
          log_print(f"{description} failed: {e}")

          if attempt == 4:
            raise

          time.sleep(10)

    def join(self):
      log_print("Waiting for Jenkins build completion")

      build = self.build
      build.block_until_complete()

      log_print(f"Build completed: {build.name}")

      def get_status():
        build.poll()
        return build.get_status()

      return self.retry(
        "Reading Jenkins status",
        get_status
      )

    def get_artifact(self, name):
      artifacts = self.build.get_artifacts()

      for artifact in artifacts:
        if artifact.filename != name:
          continue

        return self.retry(
          f"Reading Jenkins artifact {name}",
          artifact.get_data
        )

      raise FileNotFoundError(
        f"Artifact {name} is not found"
      )