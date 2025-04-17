#!/usr/bin/env python
"""Terraform discovery - parses the cloudplatform environments repo for namespace and terraform resources, and stores the results in the service catalogue"""

import os
import threading
import logging
import re
from classes.service_catalogue import ServiceCatalogue
from classes.slack import Slack

# import json
from git import Repo
from tfparse import load_from_path
from time import sleep


class Services:
  def __init__(self, sc_params, slack_params, log):
    self.slack = Slack(slack_params, log)
    self.sc = ServiceCatalogue(sc_params, log)
    self.log = log

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
  services.log.debug(f'Namespace data: {data}')
  if not ns_id:
    services.log.debug(f'Adding new namespace to SC: {data}')
    services.sc.add('namespaces', data)
  else:
    services.log.debug(f'Updating namespace in SC: {data}')
    services.sc.update('namespaces', ns_id, data)


def process_repo(component, lock, services):
  global namespaces
  for environment in component['attributes']['environments']:
    namespace = environment.get('namespace', {})
    services.log.debug(f'Processing environment/namepace: {environment["name"]}:{namespace}')
    if namespace not in namespaces:
      # Add namespace to list of namespaces being done.
      namespaces.append(namespace)
    else:
      # Skip this namespace as it's already processed.
      services.log.debug(f'skipping {namespace} namespace - already been processed')
      continue

    namespace_id = None
    sc_namespace_attributes = {}
    if sc_namespace_data := services.sc.get_record(services.sc.namespaces_get, 'name', namespace):
      sc_namespace_attributes = sc_namespace_data.get('attributes', {})
      services.log.debug(f'Namespace data: {sc_namespace_data}')
      namespace_id = sc_namespace_data.get('id')
      services.log.debug(f'Namespace ID: {namespace_id}')

    data = {'name': namespace}

    resources_dir = f'{TEMP_DIR}/namespaces/live.cloud-platform.service.justice.gov.uk/{namespace}/resources'
    if os.path.isdir(resources_dir):
      # tfparse is not thread-safe!
      with lock:
        services.log.debug(f'Thread locked for tfparse: {resources_dir}')
        parsed = load_from_path(resources_dir)
        # log.debug(json.dumps(parsed, indent=2))
      # print(json.dumps(parsed, indent=2))
      for m in parsed['module']:
        # Get terraform module version
        tf_mod_version = str()
        try:
          regex = r'(?<=[\\?]ref=)[0-9]+(\.[0-9])?(\.[0-9])?$'
          tf_mod_version = re.search(regex, m['source'])[0]
        except TypeError:
          pass

        # Look for RDS instances.
        if 'cloud-platform-terraform-rds-instance' in m['source']:
          rds_instance = m
          # Delete ID that is generated by tfparse
          del rds_instance['id']
          # Process fields
          rds_instance.update({'tf_label': rds_instance['__tfmeta']['label']})
          rds_instance.update({'tf_filename': rds_instance['__tfmeta']['filename']})
          rds_instance.update({'tf_path': rds_instance['__tfmeta']['path']})
          rds_instance.update({'tf_line_end': rds_instance['__tfmeta']['line_end']})

          # convert db_max_allocated_storage to string, as occasionally it is seen as a integer
          if 'db_max_allocated_storage' in rds_instance and isinstance(rds_instance['db_max_allocated_storage'], int):
            services.log.debug(f"Converting db_max_allocated_storage to string: {rds_instance['db_max_allocated_storage']}")
            rds_instance['db_max_allocated_storage']=str(rds_instance['db_max_allocated_storage'])

          rds_instance.update(
            {'tf_line_start': rds_instance['__tfmeta']['line_start']}
          )
          rds_instance.update({'tf_mod_version': tf_mod_version})

          # Check for existing instance in SC and update same ID if so.
          try:
            # If there are any rds instances in the existing SC data
            if sc_namespace_attributes.get('rds_instance', {}):
              # Find the RDS instance SC ID that matches
              rds_id = list(
                filter(
                  lambda rds: rds['tf_path'] == rds_instance['__tfmeta']['path'],
                  sc_namespace_attributes.get('rds_instance', {}),
                )
              )[0]['id']
              rds_instance.update({'id': rds_id})
          except IndexError:
            pass

          # Clean up field not used in post to SC
          del rds_instance['__tfmeta']
          data.update({'rds_instance': [rds_instance]})

        # Look for elasticache instances.
        if 'cloud-platform-terraform-elasticache-cluster' in m['source']:
          elasticache_cluster = m
          # Delete ID that is generated by tfparse
          del elasticache_cluster['id']
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
          if 'parameter_group_name' in elasticache_cluster and isinstance(elasticache_cluster['parameter_group_name'], dict):
            elasticache_cluster['parameter_group_name']=elasticache_cluster['parameter_group_name']['__name__']

          elasticache_cluster.update({'tf_mod_version': tf_mod_version})
          # Check for existing instance in SC and update same ID if so.
          try:
            # If there are any rds instances in the existing SC data
            if sc_namespace_attributes.get('elasticache_cluster', {}):
              # Find the elasticache cluster SC ID that matches
              elasticache_id = list(
                filter(
                  lambda elasticache: elasticache['tf_path']
                  == elasticache_cluster['__tfmeta']['path'],
                  sc_namespace_attributes.get('elasticache_cluster', {}),
                )
              )[0]['id']
              elasticache_cluster.update({'id': elasticache_id})
          except (IndexError, KeyError):
            pass

          # Clean up field not used in post to SC
          del elasticache_cluster['__tfmeta']
          data.update({'elasticache_cluster': [elasticache_cluster]})

        if 'pingdom_check' in parsed.keys():
          for r in parsed['pingdom_check']:
            # Look for pingdom checks.
            if 'http' in r['type'] and '__tfmeta' in r.keys():
              pingdom_check = r
              # Delete ID that is generated by tfparse
              del pingdom_check['id']
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
              # pingdom_check.update({'tf_mod_version': tf_mod_version})
              # Check for existing instance in SC and update same ID if so.
              try:
                # If there are any rds instances in the existing SC data
                if sc_namespace_attributes.get('pingdom_check', {}):
                  # Find the Pingdom check SC ID that matches
                  pingdom_id = list(
                    filter(
                      lambda pingdom: pingdom['tf_path']
                      == pingdom_check['__tfmeta']['path'],
                      sc_namespace_attributes.get('pingdom_check', {}),
                    )
                  )[0]['id']
                  pingdom_check.update({'id': pingdom_id})
              except IndexError:
                pass

              # Clean up field not used in post to SC
              del pingdom_check['__tfmeta']
              data.update({'pingdom_check': [pingdom_check]})

    services.log.debug(f'Namespace id:{namespace_id}, data: {data}')
    update_sc_namespace(namespace_id, data, services)

  return True


def process_components(components, services):
  services.log.info(f'Processing batch of {len(components)} components...')
  lock = threading.Lock()
  component_count = 1
  for component in components:
    t_repo = threading.local()
    t_repo = threading.Thread(
      target=process_repo, args=(component, lock, services), daemon=True
    )

    # Apply limit on total active threads
    while threading.active_count() > (MAX_THREADS - 1):
      services.log.debug(
        f'Active Threads={threading.active_count()}, Max Threads={MAX_THREADS}'
      )
      sleep(10)

    t_repo.start()
    component_name = component['attributes']['name']
    services.log.info(
      f'Started thread for {component_name} ({component_count}/{len(components)})'
    )
    component_count += 1

  t_repo.join()
  services.log.info('Completed processing components')


def main():
  logging.basicConfig(
    format='[%(asctime)s] %(levelname)s %(threadName)s %(message)s', level=LOG_LEVEL
  )
  log = logging.getLogger(__name__)

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

  services = Services(sc_params, slack_params, log)

  if not os.path.isdir(TEMP_DIR):
    try:
      cp_envs_repo = Repo.clone_from(
        'https://github.com/ministryofjustice/cloud-platform-environments.git', TEMP_DIR
      )
    except Exception as e:
      services.slack.alert(
        f'*Terraform Discovery failed*: Unable to clone cloud-platform-environments repo: {e}'
      )
      raise SystemExit()
  else:
    try:
      cp_envs_repo = Repo(TEMP_DIR)
      origin = cp_envs_repo.remotes.origin
      origin.pull()
    except Exception as e:
      services.slack.alert(
        f'*Terraform Discovery failed*: Unable to pull latest version of cloud-platform-environments repo: {e}'
      )
      raise SystemExit()

  sc_data = services.sc.get_all_records(services.sc.components_get)
  if sc_data:
    process_components(sc_data, services)


if __name__ == '__main__':
  main()
