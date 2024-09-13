import requests
import logging
import json
import os
import threading
from requests.auth import HTTPBasicAuth
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s: %(message)s')

class JiraClient:
    def __init__(self, base_url, auth_type='token', email=None, token=None, username=None, password=None):
        self.base_url = base_url
        self.auth_type = auth_type
        self.email = email
        self.token = token
        self.username = username
        self.password = password
        self.session = requests.Session()

    def get_auth(self):
        if self.auth_type == 'token':
            return HTTPBasicAuth(self.email, self.token)
        else:
            return HTTPBasicAuth(self.username, self.password)

    def get(self, endpoint, params=None, headers=None):
        url = f"{self.base_url}{endpoint}"
        response = self.session.get(url, auth=self.get_auth(),
                                    headers=headers or {"Accept": "application/json"}, params=params)
        if response.status_code != 200:
            logging.error(f"Erro ao buscar {endpoint}: {response.status_code} - {response.text}")
            return None
        return response.json()

    def fetch_issue(self, issue_key, expand=None):
        params = {'expand': expand} if expand else {}
        return self.get(f"/rest/api/2/issue/{issue_key}", params=params)

    def fetch_user(self, user_key, expand=None):
        params = {'expand': expand} if expand else {}
  
        if self.auth_type == 'token':  # Jira Cloud
            return self.get(f"/rest/api/3/user/search", params={'query': user_key, **params})
        else:
            return self.get(f"/rest/api/2/user?username={user_key}", params=params)

    def fetch_custom_fields(self):
        return self.get("/rest/api/2/field")

    def search_issues(self, jql, start_at=0, max_results=100, expand=None):
        params = {
            'jql': jql,
            'startAt': start_at,
            'maxResults': max_results,
            'expand': expand
        }
        return self.get("/rest/api/2/search", params=params)

    def fetch_project(self, project_key):
        return self.get(f"/rest/api/2/project/{project_key}")

class JiraExporter:
    MAX_FILE_SIZE_MB = 7
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
    CUSTOM_USER = "712020:e5165038-2f2b-4650-a575-e61739ca7376"
    USER_CACHE_FILE = "users_cache.txt"
    USER_ACCOUNTS_FILE = "users_accounts.txt"
    PROCESSED_ISSUES_CACHE = "processed_issues_cache.txt"
    EXEMPTED_GROUPS = ["jira-administrators"]
    ALLOWED_CUSTOM_FIELD_TYPES = [
        "com.atlassian.jira.plugin.system.customfieldtypes:textfield",
        "com.atlassian.jira.plugin.system.customfieldtypes:textarea",
        "com.atlassian.jira.plugin.system.customfieldtypes:datepicker",
        "com.atlassian.jira.plugin.system.customfieldtypes:datetime",
        "com.atlassian.jira.plugin.system.customfieldtypes:float",
        "com.atlassian.jira.plugin.system.customfieldtypes:select",
        "com.atlassian.jira.plugin.system.customfieldtypes:radiobuttons",
        "com.atlassian.jira.plugin.system.customfieldtypes:project",
        "com.atlassian.jira.plugin.system.customfieldtypes:multiversion",
        "com.atlassian.jira.plugin.system.customfieldtypes:version",
        "com.atlassian.jira.plugin.system.customfieldtypes:userpicker",
        "com.atlassian.jira.plugin.system.customfieldtypes:url",
        "com.atlassian.jira.plugin.system.customfieldtypes:multiselect",
        "com.atlassian.jira.plugin.system.customfieldtypes:multicheckboxes",
        "com.atlassian.jira.plugin.system.customfieldtypes:multiuserpicker",
        "com.atlassian.jira.plugin.system.customfieldtypes:multigrouppicker",
        "com.atlassian.jira.plugin.system.customfieldtypes:grouppicker",
        "com.atlassian.jira.plugin.system.customfieldtypes:cascadingselect",
        "com.atlassian.jira.plugin.system.customfieldtypes:readonlyfield",
        "com.atlassian.jira.plugin.system.customfieldtypes:labels",
        "com.pyxis.greenhopper.jira:gh-sprint"
    ]

    def __init__(self, jira_version, project_key, config, cloud_config):
        self.jira_version = jira_version
        self.project_key = project_key
        self.config = config
        self.cloud_config = cloud_config
        self.client = JiraClient(**config)
        self.cloud_client = self.client if jira_version == 'cloud' else JiraClient(**cloud_config)
        self.current_issue_id = 1
        self.link_id_counter = 1
        self.issue_id_map = {}
        self.issues_in_progress = set()
        self.file_lock = threading.Lock()
        self.user_cache = self.load_user_cache()
        self.user_accounts = self.load_user_accounts()
        self.mapped_issues = []
        self.issue_links = []

    def load_user_cache(self):
        user_cache = {}
        if os.path.exists(self.USER_CACHE_FILE):
            with self.file_lock:
                with open(self.USER_CACHE_FILE, 'r') as file:
                    for line in file:
                        try:
                            email, in_group = line.strip().split(',', 1)
                            user_cache[email] = in_group == 'True'
                        except ValueError:
                            continue
        return user_cache

    def load_user_accounts(self):
        user_accounts = {}
        if os.path.exists(self.USER_ACCOUNTS_FILE):
            with self.file_lock:
                with open(self.USER_ACCOUNTS_FILE, 'r') as file:
                    for line in file:
                        try:
                            email, account_id = line.strip().split(',', 1)
                            user_accounts[email] = account_id
                        except ValueError:
                            continue
        return user_accounts

    def save_user_cache(self):
        with self.file_lock:
            with open(self.USER_CACHE_FILE, 'w') as file:
                for email, in_group in self.user_cache.items():
                    file.write(f"{email},{in_group}\n")

    def save_user_accounts(self):
        with self.file_lock:
            with open(self.USER_ACCOUNTS_FILE, 'w') as file:
                for email, account_id in self.user_accounts.items():
                    file.write(f"{email},{account_id}\n")

    def is_user_in_exempted_groups(self, user_key):
        if user_key in self.user_cache:
            return self.user_cache[user_key]

        user_data = self.client.fetch_user(user_key, expand='groups')
        if not user_data:
            self.user_cache[user_key] = False
            return False

        groups = user_data.get('groups', {}).get('items', [])
        in_exempted = any(group['name'] in self.EXEMPTED_GROUPS for group in groups)
        self.user_cache[user_key] = in_exempted
        self.save_user_cache()
        return in_exempted

    def handle_user(self, user_data):
        if not user_data:
            return self.CUSTOM_USER

        user_key = user_data.get('accountId') if self.jira_version == 'cloud' else user_data.get('name')
        email = user_data.get('emailAddress')

        if self.is_user_in_exempted_groups(user_key):
            if self.jira_version == 'datacenter' and email:
                account_id = self.user_accounts.get(email)
                if not account_id:
                    account_data = self.cloud_client.fetch_user(email)
                    if account_data and isinstance(account_data, list) and account_data:
                        account_id = account_data[0].get('accountId', self.CUSTOM_USER)
                        self.user_accounts[email] = account_id
                        self.save_user_accounts()
                    else:
                        account_id = self.CUSTOM_USER
                return account_id
            return user_key
        return self.CUSTOM_USER

    def fetch_custom_fields(self):
        fields = self.client.fetch_custom_fields()
        if not fields:
            return {}
        custom_fields = {
            field['id']: {"name": field['name'], "type": field['schema']['custom']}
            for field in fields
            if field.get('schema') and field['schema'].get('custom') in self.ALLOWED_CUSTOM_FIELD_TYPES
        }
        logging.info(f"{len(custom_fields)} allowed custom field types found.")
        return custom_fields

    def fetch_issues(self):
        issues = []
        start_at = 0
        max_results = 100
        total = None

        while True:
            data = self.client.search_issues(
                jql=f'project={self.project_key} order by key desc',
                start_at=start_at,
                max_results=max_results
            )
            if not data:
                break

            issues_batch = data.get('issues', [])
            total = total or data.get('total', 0)

            for issue_summary in issues_batch:
                issue_key = issue_summary['key']
                full_issue_data = self.client.fetch_issue(issue_key)  
                if full_issue_data:
                    issues.append(full_issue_data)

            start_at += max_results

            if len(issues) >= total:
                break

        logging.info(f"Total de issues buscadas: {len(issues)} de {total}.")
        return issues

    def map_issue_details(self, issue, custom_fields):
        issue_key = issue['key']
        if issue_key in self.issues_in_progress or self.is_issue_processed(issue_key):
            logging.info(f"Issue {issue_key} already processed or in progress. Skipping.")
            return

        self.issues_in_progress.add(issue_key)
        issue_id = self.current_issue_id
        self.current_issue_id += 1
        self.issue_id_map[issue_key] = issue_id

        mapped_issue = {
            "key": issue_key,
            "externalId": str(issue_id),
            "priority": issue['fields'].get('priority', {}).get('name'),
            "description": issue['fields'].get('description', ''),
            "status": issue['fields']['status']['name'],
            "reporter": self.handle_user(issue['fields'].get('reporter')),
            "labels": issue['fields'].get('labels', []),
            "issueType": issue['fields']['issuetype']['name'],
            "resolution": issue['fields']['resolution']['name'] if issue['fields'].get('resolution') else None,
            "created": issue['fields']['created'],
            "updated": issue['fields']['updated'],
            "resolutiondate": issue['fields'].get('resolutiondate'),
            "duedate": issue['fields'].get('duedate'),
            "affectedVersions": [v['name'] for v in issue['fields'].get('versions', [])],
            "summary": issue['fields'].get('summary', ''),
            "assignee": self.handle_user(issue['fields'].get('assignee')),
            "fixedVersions": [v['name'] for v in issue['fields'].get('fixVersions', [])],
            "components": [c['name'] for c in issue['fields'].get('components', [])],
            "customFieldValues": [],
            "attachments": [],
            "comments": [],
            "history": []
        }

        self.process_custom_fields(issue, custom_fields, mapped_issue)

        self.process_issue_links(issue, issue_id)

        attachments = issue['fields'].get('attachment', [])
        mapped_issue["attachments"] = [
            {
                "name": a['filename'],
                "attacher": self.handle_user(a.get('author')),
                "created": a['created'],
                "uri": a['content'],
                "description": a.get('description', '')
            } for a in attachments
        ]

        comments = issue['fields'].get('comment', {}).get('comments', [])
        mapped_issue["comments"] = [
            {
                "body": c['body'],
                "author": self.handle_user(c.get('author')),
                "created": c['created']
            } for c in comments
        ]

        histories = issue.get('changelog', {}).get('histories', [])
        mapped_issue["history"] = [
            {
                "author": self.handle_user(h.get('author')),
                "created": h['created'],
                "items": [
                    {
                        "fieldType": i['fieldtype'],
                        "field": i['field'],
                        "from": i.get('from'),
                        "fromString": i.get('fromString'),
                        "to": i.get('to'),
                        "toString": i.get('toString')
                    } for i in h['items']
                ]
            } for h in histories
        ]

        self.mark_issue_as_processed(issue_key)
        self.issues_in_progress.remove(issue_key)
        self.mapped_issues.append(mapped_issue)
        logging.info(f"Issue {issue_key} mapeada com sucesso.")

    def process_custom_fields(self, issue, custom_fields, mapped_issue):
        for field_id, field_value in issue['fields'].items():
            if field_id.startswith("customfield_") and field_id in custom_fields and field_value:
                custom_field_info = custom_fields[field_id]
                value = self.extract_custom_field_value(field_value, custom_field_info['type'])
                mapped_issue['customFieldValues'].append({
                    "fieldName": custom_field_info['name'],
                    "fieldType": custom_field_info.get('type', 'unknown'),
                    "value": value
                })

    def extract_custom_field_value(self, field_value, field_type):
        if isinstance(field_value, dict) and 'value' in field_value:
            value = field_value['value']
        elif isinstance(field_value, list):
            value = [item['value'] if isinstance(item, dict) and 'value' in item else item for item in field_value]
        else:
            value = field_value

        if field_type == 'com.atlassian.jira.plugin.system.customfieldtypes:datetime':
            value = self.format_jira_datetime(value)

        if field_type in ['com.atlassian.jira.plugin.system.customfieldtypes:userpicker',
                          'com.atlassian.jira.plugin.system.customfieldtypes:multiuserpicker']:
            if field_type == 'com.atlassian.jira.plugin.system.customfieldtypes:userpicker':
                if isinstance(value, dict):
                    value = self.handle_user(value)
                else:
                    value = self.handle_user({'emailAddress': value})
            elif field_type == 'com.atlassian.jira.plugin.system.customfieldtypes:multiuserpicker':
                value = [self.handle_user(user) for user in field_value if isinstance(user, dict)]
        return value

    def process_issue_links(self, issue, issue_id):
        issue_links = issue['fields'].get('issuelinks', [])
        for link in issue_links:
            link_type = link['type']['name']
            if 'inwardIssue' in link:
                linked_issue_key = link['inwardIssue']['key']
            elif 'outwardIssue' in link:
                linked_issue_key = link['outwardIssue']['key']
            else:
                continue

            if linked_issue_key not in self.issue_id_map:
                linked_issue = self.client.fetch_issue(linked_issue_key)
                if linked_issue:
                    self.map_issue_details(linked_issue, self.fetch_custom_fields())

            linked_issue_id = self.issue_id_map.get(linked_issue_key)
            if linked_issue_id:
                source_id, destination_id = (issue_id, linked_issue_id) if issue_id < linked_issue_id else (linked_issue_id, issue_id)
                if not any(l['sourceId'] == str(source_id) and l['destinationId'] == str(destination_id) for l in self.issue_links):
                    self.issue_links.append({
                        "name": link_type,
                        "sourceId": str(source_id),
                        "destinationId": str(destination_id)
                    })

    def is_issue_processed(self, issue_key):
        if os.path.exists(self.PROCESSED_ISSUES_CACHE):
            with open(self.PROCESSED_ISSUES_CACHE, 'r') as file:
                return issue_key in file.read().splitlines()
        return False

    def mark_issue_as_processed(self, issue_key):
        with self.file_lock:
            with open(self.PROCESSED_ISSUES_CACHE, 'a') as file:
                file.write(f"{issue_key}\n")

    def format_jira_datetime(self, value):
        try:
            date_obj = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%f%z')
            return date_obj.strftime('%d/%b/%y %I:%M %p')
        except ValueError as e:
            logging.error(f"Error formatting date: {e}")
            return value

    def calculate_size_in_bytes(self, data):
        return len(json.dumps(data).encode('utf-8'))

    def split_issues_into_batches(self, project_details):
        batches = []
        current_batch = []
        current_size = 0

        for issue in self.mapped_issues:
            issue_size = self.calculate_size_in_bytes(issue)
            if current_size + issue_size > self.MAX_FILE_SIZE_BYTES:
                batches.append({
                    "projects": [project_details],
                    "issues": current_batch,
                    "links": self.issue_links
                })
                current_batch = []
                current_size = 0
            current_batch.append(issue)
            current_size += issue_size

        if current_batch:
            batches.append({
                "projects": [project_details],
                "issues": current_batch,
                "links": self.issue_links
            })
        return batches

    def export_issues(self):
        project_details = self.client.fetch_project(self.project_key)
        if not project_details:
            logging.error(f"Unable to fetch project details for {self.project_key}. Exiting...")
            return

        custom_fields = self.fetch_custom_fields()
        issues = self.fetch_issues()
        if not issues:
            logging.info("No issues found.")
            return

        logging.info(f"Total issues to export: {len(issues)}")

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [
                executor.submit(self.map_issue_details, issue, custom_fields)
                for issue in issues
            ]
            for future in as_completed(futures):
                future.result()

        batches = self.split_issues_into_batches(project_details)
        for idx, batch in enumerate(batches, start=1):
            output_file = f"jira_export_{self.project_key}_batch_{idx}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(batch, f, ensure_ascii=False, indent=4)
            logging.info(f"File {output_file} successfully created.")

def select_jira_version():
    print("Please select the Jira version you are using:")
    print("1. Jira Cloud")
    print("2. Jira Data Center")
    while True:
        choice = input("Enter the corresponding number (1 or 2): ").strip()
        if choice == '1':
            return 'cloud'
        elif choice == '2':
            return 'datacenter'
        else:
            print("Invalid input. Choose 1 for Jira Cloud or 2 for Jira Data Center.")

def main():
    JIRA_VERSION = select_jira_version()
    PROJECT_KEY = input("Enter the project key you want to export: ")

    # Declarar a configuração do Jira Cloud apenas uma vez
    cloud_config = {
        'email': 'rodolfobortolin@gmail.com',
        'token': '',
        'base_url': "https://domain.atlassian.net",
        'auth_type': 'token'
    }

    if JIRA_VERSION == 'cloud':
        config = cloud_config
    else:
        config = {
            'username': 'admin',
            'password': 'admin',
            'base_url': "http://localhost:8080",
            'auth_type': 'basic'
        }

    exporter = JiraExporter(JIRA_VERSION, PROJECT_KEY, config, cloud_config)
    exporter.export_issues()

if __name__ == "__main__":
    main()
