import requests
import logging
import json


class ServiceCatalogue:
  def __init__(self, params, log_level=logging.INFO):
    logging.basicConfig(
      format='[%(asctime)s] %(levelname)s %(threadName)s %(message)s', level=log_level
    )
    # default variables
    page_size = 10
    pagination_page_size = f'&pagination[pageSize]={page_size}'
    # Example Sort filter
    # sort_filter='&sort=updatedAt:asc'
    sort_filter = ''

    self.log = logging.getLogger(__name__)
    self.url = params['url']
    self.key = params['key']

    # limit results for testing/dev
    # See strapi filter syntax https://docs.strapi.io/dev-docs/api/rest/filters-locale-publication
    # Example filter string = '&filters[name][$contains]=example'
    self.filter = params.get('filter', '')

    self.product_filter = '&fields[0]=slack_channel_id&fields[1]=slack_channel_name&fields[2]=p_id&fields[3]=name'

    self.api_headers = {
      'Authorization': f'Bearer {self.key}',
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    }
    self.components = 'components'
    self.components_get = f'{self.components}?populate[0]=latest_commit&populate[1]=environments&populate[2]=product&populate[3]=envs{self.filter}{pagination_page_size}{sort_filter}'

    self.products = 'products'
    self.products_get = f'{self.products}?populate[0]=parent&populate[1]=children&populate[2]=product_set&populate[3]=service_area&populate[4]=team{self.product_filter}{pagination_page_size}{sort_filter}'

    self.github_teams = 'github-teams'
    self.environments = 'environments'
    self.environments_get = (
      f'{self.environments}?populate[0]=component{pagination_page_size}{sort_filter}'
    )
    self.connection_ok = self.test_connection()

  """
  Test connection to the Service Catalogue
  """

  def test_connection(self):
    # Test connection to Service Catalogue
    try:
      self.log.info(f'Testing connection to the Service Catalogue - {self.url}')
      r = requests.head(f'{self.url}', headers=self.api_headers, timeout=10)
      self.log.info(
        f'Successfully connected to the Service Catalogue - {self.url}. {r.status_code}'
      )
      return True
    except Exception as e:
      self.log.critical(f'Unable to connect to the Service Catalogue - {e}')
      return False

  """
  Get all multipage results from Service Catalogue
  """

  def get_all_records(self, table):
    json_data = []
    self.log.info(
      f'Getting all records from table {table} in Service Catalogue using URL: {self.url}/v1/{table}'
    )
    try:
      r = requests.get(f'{self.url}/v1/{table}', headers=self.api_headers, timeout=10)
      if r.status_code == 200:
        j_meta = r.json()['meta']['pagination']
        self.log.debug(f'Got result page: {j_meta["page"]} from Service Catalogue')
        json_data.extend(r.json()['data'])
      else:
        raise Exception(
          f'Received non-200 response from Service Catalogue: {r.status_code}'
        )

      # Loop over the remaining pages and return one at a time
      num_pages = j_meta['pageCount']
      for p in range(2, num_pages + 1):
        if '?' in table:  # add an extra parameter if there are already parameters
          page = f'&pagination[page]={p}'
        else:  # otherwise use ? to denote the first parameter
          page = f'?pagination[page]={p}'
        r = requests.get(
          f'{self.url}/v1/{table}{page}', headers=self.api_headers, timeout=10
        )
        if r.status_code == 200:
          j_meta = r.json()['meta']['pagination']
          self.log.debug(f'Got result page: {j_meta["page"]} from SC')
          json_data.extend(r.json()['data'])
        else:
          raise Exception(
            f'Received non-200 response from Service Catalogue when reading all records from {table}: {r.status_code}'
          )

    except Exception as e:
      self.log.error(
        f'Problem with Service Catalogue API while reading all records from {table}. {e}'
      )
    return json_data

  """
  Get a single record by filter parameter from the Service Catalogue
  """

  def get_record(self, table, label, parameter):
    json_data = {}
    try:
      if '?' in table:  # add an extra parameter if there are already parameters
        filter = f'&filters[{label}][$eq]={parameter}'
      else:
        filter = f'?filters[{label}][$eq]={parameter}'
      r = requests.get(
        f'{self.url}/v1/{table}{filter}', headers=self.api_headers, timeout=10
      )
      if r.status_code == 200:
        json_data = r.json()['data'][0]
      else:
        raise Exception(
          f'Received non-200 response from Service Catalogue when reading all records from {table}: {r.status_code}'
        )

    except Exception as e:
      self.log.error(
        f'Problem with Service Catalogue API while reading all records from {table}. {e}'
      )
    return json_data

  """
  Update a record in the Service Catalogue with passed-in JSON data
  """

  def update(self, table, element_id, data):
    success = False
    try:
      self.log.debug(f'data to be uploaded: {json.dumps(data, indent=2)}')
      x = requests.put(
        f'{self.url}/v1/{table}/{element_id}',
        headers=self.api_headers,
        json={'data': data},
        timeout=10,
      )
      if x.status_code == 200:
        self.log.info(
          f'Successfully updated record {element_id} in {table.split("/")[-1]}: {x.status_code}'
        )
        success = True
      else:
        self.log.info(
          f'Received non-200 response from service catalogue for record id {element_id} in {table.split("/")[-1]}: {x.status_code} {x.content}'
        )
    except Exception as e:
      self.log.error(
        f'Error updating service catalogue for record id {element_id} in {table.split("/")[-1]}: {e}'
      )
    return success

  def add(self, table, data):
    success = False
    try:
      self.log.debug(data)
      x = requests.post(
        f'{self.url}/v1/{table}',
        headers=self.api_headers,
        json={'data': data},
        timeout=10,
      )
      if x.status_code == 200:
        self.log.info(
          f'Successfully added {(data["team_name"] if "team_name" in data else data["name"])} to {table.split("/")[-1]}: {x.status_code}'
        )
        success = True
      else:
        self.log.info(
          f'Received non-200 response from service catalogue to add a record to {table.split("/")[-1]}: {x.status_code} {x.content}'
        )
    except Exception as e:
      self.log.error(
        f'Error adding a record to {table.split("/")[-1]} in service catalogue: {e}'
      )
    return success

  # eg get_id('github-teams', 'team_name', 'example')
  def get_id(self, match_table, match_field, match_string):
    try:
      r = requests.get(
        f'{self.url}/v1/{match_table}?filters[{match_field}][$eq]={match_string.replace("&", "&amp;")}',
        headers=self.api_headers,
        timeout=10,
      )
      if r.status_code == 200 and r.json()['data']:
        sc_id = r.json()['data'][0]['id']
        self.log.debug(
          f'Successfully found Service Catalogue ID for {match_field}={match_string} in {match_table}: {sc_id}'
        )
        return sc_id
      self.log.warning(
        f'Could not find Service Catalogue ID for {match_field}={match_string} in {match_table}'
      )
      return None
    except Exception as e:
      self.log.error(
        f'Error getting Service Catalogue ID for {match_field}={match_string} in {match_table}: {e} - {r.status_code} {r.content}'
      )
      return None

  def get_component_env_id(self, component, env):
    env_id = None
    for env_data in component['attributes'].get('environments'):
      if env_data['name'] == env:
        env_id = env_data['id']
        self.log.debug(
          f'Found existing environment ID for {env} in component {component["attributes"]["name"]}: {env_id}'
        )
    if not env_id:
      self.log.debug(
        f'No existing environment ID found for {env} in component {component["attributes"]["name"]}'
      )
    return env_id

  def find_all_teams_ref_in_sc(self):
    components = self.get_all_records(self.components_get)
    combined_teams = set()
    for component in components:
      attributes = component.get('attributes', {})
      combined_teams.update(attributes.get('github_project_teams_write', []) or [])
      combined_teams.update(attributes.get('github_project_teams_admin', []) or [])
      combined_teams.update(attributes.get('github_project_teams_maintain', []) or [])
    return combined_teams
