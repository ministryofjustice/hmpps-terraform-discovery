from datetime import datetime
from utilities.job_log_handling import log_debug, log_error, log_info, log_critical, job

def update(services, status):
  sc = services.sc
  sc_scheduled_jobs_data = sc.get_record( 'scheduled-jobs', 'name', job.name)
  job_data = {
    "last_scheduled_run": datetime.now().isoformat(),
    "result": status,
    "error_details":  job.error_messages
  }
  if status == 'Succeeded':
    job_data["last_successful_run"] = datetime.now().isoformat()

  try:
    job_id = sc_scheduled_jobs_data.get('documentId')
    sc.update('scheduled-jobs', job_id, job_data)
    return True
  except Exception as e:
    log_error(f"Job {job.name} not found in Service Catalogue")
    return False
  