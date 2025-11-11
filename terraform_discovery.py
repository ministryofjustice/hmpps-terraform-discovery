#!/usr/bin/env python
"""Terraform discovery - parses the cloudplatform environments repo for namespace and terraform resources, and stores the results in the service catalogue"""

import os
import threading
import re
from hmpps import ServiceCatalogue, Slack
from hmpps.services.job_log_handling import (
  log_debug,
  log_error,
  log_info,
  job,
)

# import json
from git import Repo
from tfparse import load_from_path
from concurrent.futures import ThreadPoolExecutor, as_completed


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

# global namespace to keep track of the ones that have been processed
namespaces = []


def extract_module_version(module):
  regex = r'(?<=[\\?]ref=)[0-9]+(\.[0-9])?(\.[0-9])?$'
  match = re.search(regex, module.get('source', ''))
  if match:
    # Add it to the module data to save passing it into sub-functions
    log_debug(f'Found module version: {match.group(0)}')
    return match.group(0)
  log_debug('No module version found')
  return None


def extract_cloud_platform_template(module):
  h_sc_fields = [
    'tf_label',
    'tf_line_start',
    'tf_line_end',
    'tf_path',
    'tf_filename',
    'application',
    'application_insights_instance',
    'environment_name',
    'github_repo',
    'github_team_name',
    'namespace',
    'reviewer_teams',
    'selected_branch_patterns',
    'source_template_repo',
    'protected_branches_only',
    'is_production',
    'tf_mod_version',
    'prevent_self_review',
  ]
  # Process fields
  hmpps_template = {
    key: (
      module['__tfmeta'][key.split('tf_')[1]]
      if key.startswith('tf_') and key.split('tf_')[1] in module['__tfmeta']
      else module.get(
        key,
        [] if key in ['reviewer_teams', 'selected_branch_patterns'] else None,
      )
    )
    for key in h_sc_fields
  }
  hmpps_template['namespace'] = locals().get('namespace')
  return hmpps_template


def extract_rds_instance(module):
  rd_sc_fields = [
    'tf_label',
    'db_instance_class',
    'db_engine_version',
    'rds_family',
    'is_production',
    'namespace',
    'environment_name',
    'application',
    'tf_filename',
    'tf_path',
    'tf_line_start',
    'tf_line_end',
    'db_max_allocated_storage',
    'infrastructure_support',
    'business_unit',
    'team_name',
    'tf_mod_version',
    'performance_insights_enabled',
    'allow_major_version_upgrade',
    'allow_minor_version_upgrade',
    'deletion_protection',
    'maintenance_window',
    'backup_window',
    'db_parameter',
  ]
  rds_instance = {
    key: (
      module['__tfmeta'][key.split('tf_')[1]]
      if key.startswith('tf_') and key.split('tf_')[1] in module['__tfmeta']
      else str(module[key])
      if key == 'db_max_allocated_storage' and isinstance(module.get(key), int)
      else module.get(key)
    )
    for key in rd_sc_fields
  }
  return rds_instance


def extract_elasticache_cluster(module):
  ec_sc_fields = [
    'application',
    'business_unit',
    'engine_version',
    'environment_name',
    'infrastructure_support',
    'is_production',
    'namespace',
    'node_type',
    'number_cache_clusters',
    'parameter_group_name',
    'team_name',
    'tf_label',
    'tf_filename',
    'tf_path',
    'tf_line_end',
    'tf_line_start',
    'tf_mod_version',
  ]

  # Process fields
  elasticache_cluster = {
    key: (
      module['__tfmeta'][key.split('tf_')[1]]
      if key.startswith('tf_') and key.split('tf_')[1] in module['__tfmeta']
      else module['parameter_group_name']['__name__']
      if key == 'parameter_group_name'
      and isinstance(module.get('parameter_group_name'), dict)
      else module.get(key)
    )
    for key in ec_sc_fields
  }
  return elasticache_cluster


def extract_pingdom_check(parsed):
  pingdom_checks = []
  p_sc_fields = [
    'tf_label',
    'tf_filename',
    'tf_path',
    'tf_line_start',
    'tf_line_end',
    'type',
    'name',
    'host',
    'url',
    'probefilters',
    'encryption',
    'resolution',
    'notifywhenbackup',
    'sendnotificationwhendown',
    'notifyagainevery',
    'port',
    'integrationids',
  ]

  for r in parsed['pingdom_check']:
    if 'http' in r['type'] and '__tfmeta' in r.keys():
      pingdom_check = {
        key: (
          r['__tfmeta'][key.split('tf_')[1]]
          if key.startswith('tf_') and key.split('tf_')[1] in r['__tfmeta']
          else r.get(key)
        )
        for key in p_sc_fields
      }
      # Append the processed entry to the list
      pingdom_checks.append(pingdom_check)
  return pingdom_checks


def process_repo(component, lock, services):
  global namespaces
  sc = services.sc
  for environment in component.get('envs'):
    namespace = environment.get('namespace', {})
    if namespace in namespaces:
      log_debug(f'skipping {namespace} namespace - already been processed')
      continue
      # Add namespace to list of namespaces being done.
    namespaces.append(namespace)

    log_debug(f'Processing environment/namepace: {environment.get("name")}:{namespace}')
    namespace_id = sc.get_id('namespaces', 'name', namespace)
    log_debug(f'Namespace ID: {namespace_id}')

    data = {
      'name': namespace,
      'rds_instance': [],
      'elasticache_cluster': [],
      'hmpps_template': [],
      'pingdom_check': [],
    }

    resources_dir = f'{TEMP_DIR}/namespaces/live.cloud-platform.service.justice.gov.uk/{namespace}/resources'

    # if there's no resources_dir, carry on...
    if not os.path.isdir(resources_dir):
      continue

    # tfparse is not thread-safe!
    with lock:
      log_debug(f'Thread locked for tfparse: {resources_dir}')
      parsed = load_from_path(resources_dir)
    for module in parsed['module']:
      # Get terraform module version
      module['tf_mod_version'] = extract_module_version(module)
      # Same goes for namespace
      module['namespace'] = namespace

      # Check if the namespace uses the cloud-platform-terraform-hmpps-template
      if 'cloud-platform-terraform-hmpps-template' in module.get('source'):
        data['hmpps_template'].append(extract_cloud_platform_template(module))

      # Look for RDS instances.
      if 'cloud-platform-terraform-rds-instance' in module.get('source'):
        data['rds_instance'].append(extract_rds_instance(module))

      # Look for elasticache instances.
      if 'cloud-platform-terraform-elasticache-cluster' in module.get('source'):
        data['elasticache_cluster'].append(extract_elasticache_cluster(module))

    if 'pingdom_check' in parsed.keys():
      data['pingdom_check'] = extract_pingdom_check(parsed)

    if not namespace_id:
      log_debug(f'Adding new namespace to SC: {data}')
      sc.add('namespaces', data)
      return True

    log_debug(f'Updating namespace in SC: {data}')
    sc.update('namespaces', namespace_id, data)

  return True


def process_components(components, services):
  log_info(f'Processing batch of {len(components)} components...')
  lock = threading.Lock()
  component_count = 1
  # now using ThreadPoolExecutor
  with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
    future_to_component = {
      executor.submit(process_repo, component, lock, services): component
      for component in components
    }

    for future in as_completed(future_to_component):
      component = future_to_component[future]
      component_name = component.get('name')
      try:
        future.result()
        log_info(
          f'Completed processing for {component_name} ({component_count}/{len(components)})'
        )
      except Exception as exc:
        log_error(f'Error processing {component_name}: {exc}')
      component_count += 1

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
      slack.alert(
        f'*Terraform Discovery failed*: Unable to clone cloud-platform-environments repo: {e}'
      )
      log_error(f'Unable to clone cloud-platform-environments repo: {e}')
      sc.update_scheduled_job('Failed')
      raise SystemExit()
  else:
    try:
      cp_envs_repo = Repo(TEMP_DIR)
      origin = cp_envs_repo.remotes.origin
      origin.pull()
    except Exception as e:
      slack.alert(
        f'*Terraform Discovery failed*: Unable to pull latest version of cloud-platform-environments repo: {e}'
      )
      log_error(
        f'Unable to pull latest version of cloud-platform-environments repo: {e}'
      )
      sc.update_scheduled_job('Failed')
      raise SystemExit()

  sc_data = sc.get_all_records(sc.components_get)
  if sc_data:
    process_components(sc_data, services)

  # Remove namespaces where there is no longer a corresponding Cloud Platforms one
  # Build a list of cloud platforms namespaces
  base_dir = f'{TEMP_DIR}/namespaces/live.cloud-platform.service.justice.gov.uk'
  try:
    cp_namespaces = [
      d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))
    ]
  except FileNotFoundError:
    cp_namespaces = []
    log_error(f'Base namespace directory not found: {base_dir}')

  if sc_namespaces := sc.get_all_records('namespaces'):
    for namespace in sc_namespaces:
      if namespace.get('name') not in cp_namespaces:
        log_info(
          f'{namespace.get("name")} not found in Cloud Platforms Environments - removing from Service Catalogue'
        )
        sc.delete('namespaces', namespace.get('documentId'))
  else:
    log_error('Failed to get namespace data from Service Catalogue')

  if job.error_messages:
    sc.update_scheduled_job('Errors')
    log_info('Terraform discovery job completed with errors.')
  else:
    sc.update_scheduled_job('Succeeded')
    log_info('Terraform discovery job completed successfully.')


if __name__ == '__main__':
  main()
