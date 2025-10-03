#!/usr/bin/env python
"""Terraform discovery - parses the cloudplatform environments repo for namespace and terraform resources, and stores the results in the service catalogue"""

import os
import threading
import re
from classes.service_catalogue import ServiceCatalogue
from classes.slack import Slack
import processes.scheduled_jobs as sc_scheduled_job
from utilities.job_log_handling import log_debug, log_error, log_info, log_critical, log_warning, job

# import json
from git import Repo
from tfparse import load_from_path
from time import sleep

class Services:
  def __init__(self, sc_params, slack_params):
    self.slack = Slack(slack_params)
    self.sc = ServiceCatalogue(sc_params)

    if not self.sc.connection_ok:
      self.slack.alert(
        '*Terraform Discovery failed*: Unable to connect to the Service Catalogue'
      )
      raise SystemExit()


# Set maximum number of concurrent threads to run, try to avoid secondary github api limits.
MAX_THREADS = 10
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
TEMP_DIR = os.getenv('TEMP_DIR', '/tmp/cp_envs')
namespaces = []


def update_sc_namespace(ns_id, data, services):
  sc = services.sc
  log_debug(f'Namespace data: {data}')
  if not ns_id:
    log_debug(f'Adding new namespace to SC: {data}')
    sc.add('namespaces', data)
  else:
    log_debug(f'Updating namespace in SC: {data}')
    sc.update('namespaces', ns_id, data)


def process_repo(component, lock, services):
  global namespaces
  sc = services.sc
  for environment in component.get('envs'):
    namespace = environment.get('namespace', {})
    log_debug(
      f'Processing environment/namepace: {environment.get("name")}:{namespace}'
    )
    if namespace not in namespaces:
      # Add namespace to list of namespaces being done.
      namespaces.append(namespace)
    else:
      # Skip this namespace as it's already processed.
      log_debug(f'skipping {namespace} namespace - already been processed')
      continue

    namespace_id = None
    if sc_namespace_data := sc.get_record(
      sc.namespaces_get, 'name', namespace
    ):
      log_debug(f'Namespace data: {sc_namespace_data}')
      namespace_id = sc_namespace_data.get('documentId')
      log_debug(f'Namespace ID: {namespace_id}')

    data = {'name': namespace, 'rds_instance': [], 'elasticache_cluster': [], 'hmpps_template': [], 'pingdom_check': []}

    resources_dir = f'{TEMP_DIR}/namespaces/live.cloud-platform.service.justice.gov.uk/{namespace}/resources'

    if os.path.isdir(resources_dir):
      # tfparse is not thread-safe!
      with lock:
        log_debug(f'Thread locked for tfparse: {resources_dir}')
        parsed = load_from_path(resources_dir)
      for m in parsed['module']:
        # Get terraform module version
        tf_mod_version = str()
        try:
          regex = r'(?<=[\\?]ref=)[0-9]+(\.[0-9])?(\.[0-9])?$'
          tf_mod_version = re.search(regex, m['source'])[0]
        except TypeError:
          pass
        
        # Check if the namespace uses the cloud-platform-terraform-hmpps-template
        if 'cloud-platform-terraform-hmpps-template' in m['source']:
          h_sc_fields = hmpps_template_fields = ["tf_label", "tf_line_start", "tf_line_end", "tf_path", "tf_filename", "application", "application_insights_instance", "environment_name", "github_repo", "github_team_name",
            "namespace", "reviewer_teams", "selected_branch_patterns", "source_template_repo", "protected_branches_only", "is_production", "tf_mod_version", "prevent_self_review"]
          # Process fields
          hmpps_template = {
            key: (
              m["__tfmeta"][key.split("tf_")[1]] if key.startswith("tf_") and key.split("tf_")[1] in m["__tfmeta"]
              else m.get(key, [] if key in ["reviewer_teams", "selected_branch_patterns"] else None)
            )
            for key in h_sc_fields
          }
          hmpps_template["namespace"] = locals().get("namespace")
          hmpps_template["tf_mod_version"] = tf_mod_version
          if 'hmpps_template' in data:
            data['hmpps_template'].append(hmpps_template)

        # Look for RDS instances.
        if 'cloud-platform-terraform-rds-instance' in m['source']:
          rds_instance = m
          rd_sc_fields = [
              "tf_label", "db_instance_class", "db_engine_version", "rds_family", "is_production", 
              "namespace", "environment_name", "application", "tf_filename", "tf_path", 
              "tf_line_start", "tf_line_end", "db_max_allocated_storage", "infrastructure_support", 
              "business_unit", "team_name", "tf_mod_version", "performance_insights_enabled", 
              "allow_major_version_upgrade", "allow_minor_version_upgrade", "deletion_protection", 
              "maintenance_window", "backup_window", "db_parameter"
          ]
          rds_instance = {
            key: (
              m["__tfmeta"][key.split("tf_")[1]] if key.startswith("tf_") and key.split("tf_")[1] in m["__tfmeta"]
              else str(m[key]) if key == "db_max_allocated_storage" and isinstance(m.get(key), int)
              else tf_mod_version if key == "tf_mod_version"
              else m.get(key)
            )
            for key in rd_sc_fields
          }
          data["rds_instance"].append(rds_instance)

        # Look for elasticache instances.
        if 'cloud-platform-terraform-elasticache-cluster' in m['source']:
          ec_sc_fields = [         "application","business_unit","engine_version","environment_name","infrastructure_support", "is_production", "namespace","node_type", "number_cache_clusters",
            "parameter_group_name","team_name","tf_label","tf_filename","tf_path","tf_line_end","tf_line_start","tf_mod_version"]
          elasticache_cluster = m
          # Process fields
          elasticache_cluster = {
            key: (
              m["__tfmeta"][key.split("tf_")[1]] if key.startswith("tf_") and key.split("tf_")[1] in m["__tfmeta"]
              else m["parameter_group_name"]["__name__"] if key == "parameter_group_name" and isinstance(m.get("parameter_group_name"), dict)
              else tf_mod_version if key == "tf_mod_version"
              else m.get(key)
            )
            for key in ec_sc_fields
          }
          data['elasticache_cluster'].append(elasticache_cluster)
      
      if 'pingdom_check' in parsed.keys():
        p_sc_fields = [
          "tf_label", "tf_filename", "tf_path", "tf_line_start", "tf_line_end", "type", "name",
          "host", "url", "probefilters", "encryption", "resolution", "notifywhenbackup",
          "sendnotificationwhendown", "notifyagainevery", "port", "integrationids"
        ]

        for r in parsed['pingdom_check']:
          if 'http' in r['type'] and '__tfmeta' in r.keys():
            pingdom_check = {
              key: (
                r["__tfmeta"][key.split("tf_")[1]] if key.startswith("tf_") and key.split("tf_")[1] in r["__tfmeta"]
                else r.get(key)
              )
              for key in p_sc_fields
            }
            # Append the processed entry to the list
            data['pingdom_check'].append(pingdom_check)

    log_debug(f'Namespace id:{namespace_id}, data: {data}')
    update_sc_namespace(namespace_id, data, services)

  return True


def process_components(components, services):
  log_info(f'Processing batch of {len(components)} components...')
  lock = threading.Lock()
  component_count = 1
  for component in components:
    t_repo = threading.local()
    t_repo = threading.Thread(
      target=process_repo, args=(component, lock, services), daemon=True
    )

    # Apply limit on total active threads
    while threading.active_count() > (MAX_THREADS - 1):
      log_debug(
        f'Active Threads={threading.active_count()}, Max Threads={MAX_THREADS}'
      )
      sleep(10)

    t_repo.start()
    component_name = component.get('name')
    log_info(
      f'Started thread for {component_name} ({component_count}/{len(components)})'
    )
    component_count += 1

  t_repo.join()
  log_info('Completed processing components')


def main():
  slack_params = {
    'token': os.getenv('SLACK_BOT_TOKEN'),
    'notify_channel': os.getenv('SLACK_NOTIFY_CHANNEL', ''),
    'alert_channel': os.getenv('SLACK_ALERT_CHANNEL', ''),
  }

  # service catalogue parameters
  sc_params = {
    'url': os.getenv('SERVICE_CATALOGUE_API_ENDPOINT'),
    'key': os.getenv('SERVICE_CATALOGUE_API_KEY'),
    'filter': os.getenv('SC_FILTER', ''),
  }

  job.name = 'hmpps-terraform-discovery'
  services = Services(sc_params, slack_params)
  sc = services.sc
  slack = services.slack
  if not os.path.isdir(TEMP_DIR):
    try:
      cp_envs_repo = Repo.clone_from(
        'https://github.com/ministryofjustice/cloud-platform-environments.git', TEMP_DIR
      )
    except Exception as e:
      slack.alert(f'*Terraform Discovery failed*: Unable to clone cloud-platform-environments repo: {e}')
      log_error(f'Unable to clone cloud-platform-environments repo: {e}')
      sc_scheduled_job.update(services, 'Failed')
      raise SystemExit()
  else:
    try:
      cp_envs_repo = Repo(TEMP_DIR)
      origin = cp_envs_repo.remotes.origin
      origin.pull()
    except Exception as e:
      slack.alert(f'*Terraform Discovery failed*: Unable to pull latest version of cloud-platform-environments repo: {e}')
      log_error(f'Unable to pull latest version of cloud-platform-environments repo: {e}')
      sc_scheduled_job.update(services, 'Failed')
      raise SystemExit()

  sc_data = sc.get_all_records(sc.components_get)
  if sc_data:
    process_components(sc_data, services)

  if job.error_messages:
    sc_scheduled_job.update(services, 'Errors')
    log_info("Terraform discovery job completed  with errors.")
  else:
    sc_scheduled_job.update(services, 'Succeeded')
    log_info("Terraform discovery job completed successfully.")


if __name__ == '__main__':
  main()
