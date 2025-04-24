# Description: Update the status of a scheduled job in the Service Catalogue
import os
from time import sleep
from datetime import datetime
from classes.service_catalogue import ServiceCatalogue
import globals

log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()

class Services:
  def __init__(self, sc_params, slack_params, log):
    self.sc = ServiceCatalogue(sc_params, log)
    self.log = log

def process_sc_scheduled_jobs(status):
  sc = globals.services.sc
  log = globals.services.log
  sc_scheduled_jobs_data = sc.get_all_records(sc.scheduled_jobs_get)
  job_data = {
    "last_scheduled_run": datetime.now().isoformat(),
    "result": status,
    "error_details": globals.error_messages
  }
  if status == 'Succeeded':
    job_data["last_successful_run"] = datetime.now().isoformat()

  sc_scheduled_jobs_dict = {job['attributes']['name']: job for job in sc_scheduled_jobs_data}
  if globals.job_name in sc_scheduled_jobs_dict:
    sc_scheduled_job = sc_scheduled_jobs_dict[globals.job_name]
    sc_scheduled_job_id = sc_scheduled_job['id']
    sc.update('scheduled-jobs', sc_scheduled_job_id, job_data)
  else:
    log.error(f"Job {globals.job_name} not found in Service Catalogue")
    return False
  return True