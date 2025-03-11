import requests
from base64 import b64decode
import json
import yaml
from github import Auth, Github
from github.GithubException import UnknownObjectException
import logging
from datetime import datetime, timedelta, timezone
import jwt


class GithubSession:
  def __init__(self, params, log_level=logging.INFO):
    logging.basicConfig(
      format='[%(asctime)s] %(levelname)s %(threadName)s %(message)s', level=log_level
    )
    self.log = logging.getLogger(__name__)
    self.private_key = b64decode(params['app_private_key']).decode('ascii')
    self.app_id = params['app_id']
    self.app_installation_id = params['app_installation_id']

    self.auth()
    if self.session:
      try:
        rate_limit = self.session.get_rate_limit()
        self.core_rate_limit = rate_limit.core
        self.log.info(f'Github API: {rate_limit}')
        # test fetching organisation name
        self.org = self.session.get_organization('ministryofjustice')
      except Exception as e:
        self.log.critical('Unable to get Github Organisation.')

  def auth(self):
    try:
      auth = Auth.Token(self.get_access_token())
      self.session = Github(auth=auth, pool_size=50)
    except Exception as e:
      self.log.critical('Unable to connect to the github API.')

  def get_access_token(self):
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    payload = {'iat': now, 'exp': now + timedelta(minutes=10), 'iss': self.app_id}
    jwt_token = jwt.encode(payload, self.private_key, algorithm='RS256')
    headers = {
      'Authorization': f'Bearer {jwt_token}',
      'Accept': 'application/vnd.github.v3+json',
    }
    response = requests.post(
      f'https://api.github.com/app/installations/{self.app_installation_id}/access_tokens',
      headers=headers,
    )
    response.raise_for_status()
    return response.json()['token']

  def test_connection(self):
    # Test auth and connection to github
    try:
      rate_limit = self.session.get_rate_limit()
      self.core_rate_limit = rate_limit.core
      self.log.info(f'Github API: {rate_limit}')
      # test fetching organisation name
      self.org = self.session.get_organization('ministryofjustice')
      return True
    except Exception as e:
      self.log.critical('Unable to connect to the github API.')
      raise SystemExit(e) from e
      return None

  def get_rate_limit(self):
    try:
      if self.session:
        return self.session.get_rate_limit().core
    except Exception as e:
      self.log.error(f'Error getting rate limit: {e}')
      return None

  def get_org_repo(self, repo_name):
    repo = None
    try:
      repo = self.org.get_repo(repo_name)
    except Exception as e:
      self.log.error(f'Error trying to get the repo {repo_name} from Github: {e}')
      return None
    return repo

  def get_file_yaml(self, repo, path):
    try:
      file_contents = repo.get_contents(path)
      contents = b64decode(file_contents.content).decode().replace('\t', '  ')
      yaml_contents = yaml.safe_load(contents)
      return yaml_contents
    except UnknownObjectException:
      self.log.debug(f'404 File not found {repo.name}:{path}')
    except Exception as e:
      self.log.error(f'Error getting yaml file ({path}): {e}')

  def get_file_json(self, repo, path):
    try:
      file_contents = repo.get_contents(path)
      json_contents = json.loads(b64decode(file_contents.content))
      return json_contents
    except UnknownObjectException:
      self.log.debug(f'404 File not found {repo.name}:{path}')
      return None
    except Exception as e:
      self.log.error(f'Error getting json file ({path}): {e}')
      return None

  def get_file_plain(self, repo, path):
    try:
      file_contents = repo.get_contents(path)
      plain_contents = b64decode(file_contents.content).decode()
      return plain_contents
    except UnknownObjectException:
      self.log.debug(f'404 File not found {repo.name}:{path}')
      return None
    except Exception as e:
      self.log.error(f'Error getting contents from file ({path}): {e}')
      return None
