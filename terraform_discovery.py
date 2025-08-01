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

    data = {'name': namespace}

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
          hmpps_template = m
          # Process fields
          hmpps_template.update({'tf_label': hmpps_template['__tfmeta']['label']})
          hmpps_template.update({'tf_line_start': hmpps_template['__tfmeta']['line_start']})
          hmpps_template.update({'tf_line_end': hmpps_template['__tfmeta']['line_end']})
          hmpps_template.update({'tf_path': hmpps_template['__tfmeta']['path']})
          hmpps_template.update({'tf_filename': hmpps_template['__tfmeta']['filename']})
          hmpps_template.update({'tf_mod_version': tf_mod_version})
          hmpps_template.update({'application': hmpps_template['application']})
          hmpps_template.update({'application_insights_instance': hmpps_template['application_insights_instance'] if 'application_insights_instance' in hmpps_template else None})
          hmpps_template.update({'environment_name': hmpps_template['environment']})
          hmpps_template.update({'github_repo': hmpps_template['github_repo']})
          hmpps_template.update({'github_team_name': hmpps_template['github_team'] if 'github_team' in hmpps_template else None})
          hmpps_template.update({'is_production': hmpps_template['is_production'] })
          hmpps_template.update({'namespace': namespace if 'namespace' in locals() else None})
          hmpps_template.update({'reviewer_teams': hmpps_template['reviewer_teams'] if 'reviewer_teams' in hmpps_template and hmpps_template['reviewer_teams'] else []})
          hmpps_template.update({'selected_branch_patterns': hmpps_template['selected_branch_patterns'] if 'selected_branch_patterns' in hmpps_template else []})
          hmpps_template.update({'source_template_repo': hmpps_template['source_template_repo'] if 'source_template_repo' in hmpps_template else None})
          hmpps_template.update({'protected_branches_only': hmpps_template['protected_branches_only'] if 'protected_branches_only' in hmpps_template else None})
          hmpps_template.update({'prevent_self_review': hmpps_template['prevent_self_review'] if 'prevent_self_review' in hmpps_template else None})
          hmpps_template = {key: value for key, value in hmpps_template.items() if key in h_sc_fields}
          # Clean up field not used in post to SC
          if 'hmpps_template' in data:
            data['hmpps_template'].append(hmpps_template)
          else:
            data['hmpps_template'] = []

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
          # Process fields
          rds_instance.update({'tf_label': rds_instance['__tfmeta']['label']})
          rds_instance.update({'tf_filename': rds_instance['__tfmeta']['filename']})
          rds_instance.update({'tf_path': rds_instance['__tfmeta']['path']})
          rds_instance.update({'tf_line_end': rds_instance['__tfmeta']['line_end']})

          # convert db_max_allocated_storage to string, as occasionally it is seen as a integer
          if 'db_max_allocated_storage' in rds_instance and isinstance(
            rds_instance['db_max_allocated_storage'], int
          ):
            log_debug(
              f'Converting db_max_allocated_storage to string: {rds_instance["db_max_allocated_storage"]}'
            )
            rds_instance['db_max_allocated_storage'] = str(
              rds_instance['db_max_allocated_storage']
            )

          rds_instance.update({'tf_line_start': rds_instance['__tfmeta']['line_start']})
          rds_instance.update({'tf_mod_version': tf_mod_version})

          rds_instance = {key: value for key, value in rds_instance.items() if key in rd_sc_fields}
          data.update({'rds_instance': [rds_instance]})

        # Look for elasticache instances.
        if 'cloud-platform-terraform-elasticache-cluster' in m['source']:
          ec_sc_fields = [         "application","business_unit","engine_version","environment_name","infrastructure_support", "is_production", "namespace","node_type", "number_cache_clusters",
            "parameter_group_name","team_name","tf_label","tf_filename","tf_path","tf_line_end","tf_line_start","tf_mod_version"]
          elasticache_cluster = m
          # Process fields
          elasticache_cluster.update(
            {'tf_label': elasticache_cluster['__tfmeta']['label']}
          )
          elasticache_cluster.update(
            {'tf_filename': elasticache_cluster['__tfmeta']['filename']}
          )
          elasticache_cluster.update(
            {'tf_path': elasticache_cluster['__tfmeta']['path']}
          )
          elasticache_cluster.update(
            {'tf_line_end': elasticache_cluster['__tfmeta']['line_end']}
          )
          elasticache_cluster.update(
            {'tf_line_start': elasticache_cluster['__tfmeta']['line_start']}
          )

          # if parameter_group_name refers to another tf resource, get the name of the resource.
          if 'parameter_group_name' in elasticache_cluster and isinstance(
            elasticache_cluster['parameter_group_name'], dict
          ):
            elasticache_cluster['parameter_group_name'] = elasticache_cluster[
              'parameter_group_name'
            ]['__name__']

          elasticache_cluster.update({'tf_mod_version': tf_mod_version})
          elasticache_cluster.pop('auth_token_rotated_date', None)
          elasticache_cluster.pop('providers', None)
          elasticache_cluster.pop('source', None)
          elasticache_cluster.pop('vpc_name', None)
          elasticache_cluster = {key: value for key, value in elasticache_cluster.items() if key in ec_sc_fields}
          data.update({'elasticache_cluster': [elasticache_cluster]})

        if 'pingdom_check' in parsed.keys():
          p_sc_fields = [ "tf_label","tf_filename","tf_path","tf_line_start","tf_line_end","type","name", "host", "url","probefilters","encryption",
              "resolution", "notifywhenbackup","sendnotificationwhendown","notifyagainevery","port","integrationids"]
          for r in parsed['pingdom_check']:
            # Look for pingdom checks.
            if 'http' in r['type'] and '__tfmeta' in r.keys():
              pingdom_check = r
              # Process fields
              pingdom_check.update({'tf_label': pingdom_check['__tfmeta']['label']})
              pingdom_check.update(
                {'tf_filename': pingdom_check['__tfmeta']['filename']}
              )
              pingdom_check.update({'tf_path': pingdom_check['__tfmeta']['path']})
              pingdom_check.update(
                {'tf_line_end': pingdom_check['__tfmeta']['line_end']}
              )
              pingdom_check.update(
                {'tf_line_start': pingdom_check['__tfmeta']['line_start']}
              )
              pingdom_check = {key: value for key, value in pingdom_check.items() if key in p_sc_fields}
              data.update({'pingdom_check': [pingdom_check]})

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
