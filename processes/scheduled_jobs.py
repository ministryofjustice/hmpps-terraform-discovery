# Description: Update the status of a scheduled job in the Service Catalogue
import os
from datetime import datetime
import globals

log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()

def update(services, status):
  sc = services.sc
  log = services.log
  sc_scheduled_jobs_data = sc.get_all_records(sc.scheduled_jobs_get)
  job_data = {
    "last_scheduled_run": datetime.now().isoformat(),
    "result": status,
    "error_details":  globals.error_messages
  }
  if status == 'Succeeded':
    job_data["last_successful_run"] = datetime.now().isoformat()

  sc_scheduled_job = next(
    (job for job in sc_scheduled_jobs_data if job['attributes']['name'] == globals.job_name), 
     None
  )

  if sc_scheduled_job:
    sc.update('scheduled-jobs', sc_scheduled_job['id'], job_data)
    return True
  else:
    log.error(f"Job {self.job_name} not found in Service Catalogue")
    return False