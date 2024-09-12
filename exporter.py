import requests
import logging
import json
import os
import threading
from requests.auth import HTTPBasicAuth
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s: %(message)s')

# Global variables and constants
MAX_FILE_SIZE_MB = 7
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
PROJECT_KEY = input("Enter the project key you want to export: ")

config = {
    'email': 'rodolfobortolin@gmail.com',
    'token': '',
    'base_url': "https://bortolin.atlassian.net",
}

CUSTOM_COMMENT_AUTHOR = "712020:e5165038-2f2b-4650-a575-e61739ca7376"
USER_CACHE_FILE = "users_cache.txt"
PROCESSED_ISSUES_CACHE = "processed_issues_cache.txt"
EXEMPTED_GROUPS = ["org-admins"]

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

file_lock = threading.Lock()
issues_in_progress = set()
issue_id_map = {}
current_issue_id = 1
link_id_counter = 1

def get_next_issue_id():
    global current_issue_id
    issue_id = current_issue_id
    current_issue_id += 1
    return issue_id

def get_next_link_id():
    global link_id_counter
    link_id = link_id_counter
    link_id_counter += 1
    return link_id

def load_user_cache():
    user_cache = {}
    if os.path.exists(USER_CACHE_FILE):
        with file_lock:
            with open(USER_CACHE_FILE, 'r') as file:
                for line in file:
                    try:
                        user_key, is_in_group = line.strip().split(',', 1)
                        user_cache[user_key] = is_in_group
                    except ValueError:
                        continue
    return user_cache

def save_user_cache(user_cache):
    with file_lock:
        with open(USER_CACHE_FILE, 'w') as file:
            for user_key, is_in_group in user_cache.items():
                file.write(f"{user_key},{is_in_group}\n")

def cache_user_group(user_key, in_exempted_groups, user_cache):
    if user_key not in user_cache:
        user_cache[user_key] = str(in_exempted_groups)
        save_user_cache(user_cache)

def is_user_in_exempted_groups(user_key, user_cache):
    if user_key in user_cache:
        return user_cache[user_key] == 'True'
    in_exempted_groups = fetch_user_group_membership(user_key)
    cache_user_group(user_key, in_exempted_groups, user_cache)
    return in_exempted_groups

def fetch_user_group_membership(user_key):
    url = f"{config['base_url']}/rest/api/2/user?accountId={user_key}&expand=groups"
    response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                            headers={"Accept": "application/json"})
    if response.status_code != 200:
        logging.error(f"Error fetching groups for user {user_key}: {response.status_code}")
        return False
    user_data = response.json()
    groups = [group['name'] for group in user_data['groups']['items']]
    return any(group in EXEMPTED_GROUPS for group in groups)

def is_issue_processed(issue_key):
    if os.path.exists(PROCESSED_ISSUES_CACHE):
        with open(PROCESSED_ISSUES_CACHE, 'r') as file:
            for line in file:
                if line.strip() == issue_key:
                    return True
    return False

def mark_issue_as_processed(issue_key):
    with open(PROCESSED_ISSUES_CACHE, 'a') as file:
        file.write(f"{issue_key}\n")

def format_jira_datetime(value):
    try:
        date_obj = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%f%z')
        return date_obj.strftime('%d/%b/%y %I:%M %p')
    except ValueError as e:
        logging.error(f"Error formatting date: {e}")
        return value

def fetch_issue_by_key(issue_key):
    url = f"{config['base_url']}/rest/api/2/issue/{issue_key}"
    params = {
        'expand': 'changelog,comment,issuelinks'
    }
    response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                            headers={"Accept": "application/json"}, params=params)
    if response.status_code != 200:
        logging.error(f"Error fetching issue {issue_key}: {response.status_code}")
        return None
    return response.json()

def fetch_issue_comments(issue_key):
    url = f"{config['base_url']}/rest/api/2/issue/{issue_key}?fields=comment&expand=comment"
    response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                            headers={"Accept": "application/json"})
    if response.status_code != 200:
        logging.error(f"Error fetching comments for issue {issue_key}: {response.status_code}")
        return []
    issue_data = response.json()
    return issue_data['fields']['comment']['comments'] if 'comment' in issue_data['fields'] else []

def handle_user(user_key, user_cache):
    if is_user_in_exempted_groups(user_key, user_cache):
        return user_key
    return CUSTOM_COMMENT_AUTHOR

def map_issue_details(issue, custom_fields, mapped_issues, user_cache, issue_links):
    issue_key = issue['key']

    if issue_key in issues_in_progress:
        logging.info(f"Issue {issue_key} is already in progress. Skipping.")
        return None
    if is_issue_processed(issue_key):
        logging.info(f"Issue {issue_key} is already processed. Skipping.")
        return None

    issues_in_progress.add(issue_key)
    issue_id = get_next_issue_id()
    issue_id_map[issue_key] = issue_id

    mapped_issue = {
        "key": issue_key,
        "externalId": str(issue_id),
        "priority": issue['fields']['priority']['name'] if issue['fields'].get('priority') else None,
        "description": issue['fields'].get('description', ''),
        "status": issue['fields']['status']['name'],
        "reporter": issue['fields']['reporter']['displayName'] if issue['fields'].get('reporter') else CUSTOM_COMMENT_AUTHOR,
        "labels": issue['fields'].get('labels', []),
        "issueType": issue['fields']['issuetype']['name'],
        "resolution": issue['fields']['resolution']['name'] if issue['fields'].get('resolution') else None,
        "created": issue['fields']['created'],
        "updated": issue['fields']['updated'],
        "resolutiondate": issue['fields'].get('resolutiondate'),
        "duedate": issue['fields'].get('duedate'),
        "affectedVersions": [v['name'] for v in issue['fields'].get('versions', [])],
        "summary": issue['fields']['summary'],
        "assignee": issue['fields']['assignee']['displayName'] if issue['fields'].get('assignee') else CUSTOM_COMMENT_AUTHOR,
        "fixedVersions": [v['name'] for v in issue['fields'].get('fixVersions', [])],
        "components": [c['name'] for c in issue['fields'].get('components', [])],
        "customFieldValues": [],
        "attachments": [],
        "comments": [],
        "history": []
    }

    if 'issuelinks' in issue['fields'] and issue['fields']['issuelinks']:
        for link in issue['fields']['issuelinks']:
            link_type = link['type']['name']
            if 'inwardIssue' in link:
                linked_issue_key = link['inwardIssue']['key']
            elif 'outwardIssue' in link:
                linked_issue_key = link['outwardIssue']['key']
            else:
                continue

            if linked_issue_key not in issue_id_map:
                linked_issue = fetch_issue_by_key(linked_issue_key)
                if linked_issue:
                    map_issue_details(linked_issue, custom_fields, mapped_issues, user_cache, issue_links)

            linked_issue_id = issue_id_map.get(linked_issue_key, None)
            if linked_issue_id:
                source_id, destination_id = (issue_id, linked_issue_id) if issue_id < linked_issue_id else (linked_issue_id, issue_id)

                if any(l['sourceId'] == str(source_id) and l['destinationId'] == str(destination_id) for l in issue_links):
                    logging.info(f"Link between {issue_key} and {linked_issue_key} already exists in the correct direction. Skipping.")
                    continue

                issue_links.append({
                    "name": link_type,
                    "sourceId": str(source_id),
                    "destinationId": str(destination_id)
                })

    for field_id, field_value in issue['fields'].items():
        if field_id.startswith("customfield_") and field_id in custom_fields and field_value:
            custom_field_info = custom_fields[field_id]
            value = field_value['value'] if isinstance(field_value, dict) and 'value' in field_value else field_value
            mapped_issue['customFieldValues'].append({
                "fieldName": custom_field_info['name'],
                "fieldType": custom_field_info.get('type', 'unknown'),
                "value": value
            })

    if 'attachment' in issue['fields'] and issue['fields']['attachment']:
        mapped_issue["attachments"] = [
            {
                "name": a['filename'],
                "attacher": handle_user(a['author']['accountId'], user_cache),
                "created": a['created'],
                "uri": a['content'],
                "description": a.get('description', '')
            } for a in issue['fields']['attachment']
        ]

    comments = fetch_issue_comments(issue['key'])
    if comments:
        mapped_issue["comments"] = [
            {
                "body": c['body'],
                "author": handle_user(c['author']['accountId'], user_cache),
                "created": c['created']
            } for c in comments
        ]

    if 'changelog' in issue and issue['changelog'].get('histories'):
        mapped_issue["history"] = [
            {
                "author": handle_user(h['author']['accountId'], user_cache) if h.get('author') else CUSTOM_COMMENT_AUTHOR,
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
            } for h in issue['changelog']['histories']
        ]

    mark_issue_as_processed(issue_key)
    issues_in_progress.remove(issue_key)
    logging.info(f"Issue {issue_key} mapped successfully.")
    return mapped_issue

def fetch_custom_fields():
    url = f"{config['base_url']}/rest/api/2/field"
    response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                            headers={"Accept": "application/json"})
    if response.status_code != 200:
        logging.error(f"Error fetching custom fields: {response.status_code}")
        return {}
    fields = response.json()
    custom_fields = {field['id']: {"name": field['name'], "type": field['schema']['custom']}
                     for field in fields
                     if field.get('schema') and field['schema'].get('custom')
                     and field['schema']['custom'] in ALLOWED_CUSTOM_FIELD_TYPES}
    logging.info(f"{len(custom_fields)} custom fields found with allowed types.")
    return custom_fields

def fetch_issues(project_key):
    issues = []
    start_at = 0
    max_results = 100
    
    url = f"{config['base_url']}/rest/api/2/search"
    params = {
        'jql': f'project={project_key} order by key desc',
        'startAt': start_at,
        'maxResults': max_results,
        'expand': 'changelog'
    }
    response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                            headers={"Accept": "application/json"}, params=params)
    
    if response.status_code != 200:
        logging.error(f"Error fetching issues: {response.status_code}")
        return []
    
    data = response.json()
    total = data['total']
    issues.extend(data['issues'])
    
    logging.info(f"Fetched {len(issues)} of {total} issues (initial batch).")

    def fetch_issue_batch(start_at):
        params['startAt'] = start_at
        response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                                headers={"Accept": "application/json"}, params=params)
        if response.status_code != 200:
            logging.error(f"Error fetching issues at startAt {start_at}: {response.status_code}")
            return []
        data = response.json()
        logging.info(f"Fetched {len(data['issues'])} issues starting at {start_at}.")
        return data['issues']

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for start_at in range(max_results, total, max_results):
            futures.append(executor.submit(fetch_issue_batch, start_at))

        for future in as_completed(futures):
            batch_issues = future.result()
            if batch_issues:
                issues.extend(batch_issues)

    logging.info(f"Total issues fetched: {len(issues)} of {total}.")
    return issues

def map_issues_in_parallel(issues, custom_fields, user_cache):
    mapped_issues = []
    issue_links = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(map_issue_details, issue, custom_fields, mapped_issues, user_cache, issue_links): issue for issue in issues}
        for future in as_completed(futures):
            mapped_issue = future.result()
            if mapped_issue:
                mapped_issues.append(mapped_issue)

    return mapped_issues, issue_links

def fetch_project_details(project_key):
    url = f"{config['base_url']}/rest/api/2/project/{project_key}"
    response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                            headers={"Accept": "application/json"})
    if response.status_code != 200:
        logging.error(f"Error fetching project details for {project_key}: {response.status_code}")
        return None
    project_data = response.json()
    logging.info(f"Project details for {project_key} retrieved successfully.")
    return project_data

def calculate_size_in_bytes(data):
    return len(json.dumps(data).encode('utf-8'))

def split_issues_into_batches(issues, max_size_bytes, project_details, custom_fields, issue_links):
    batches = []
    current_batch = []
    current_size = 0

    for issue in issues:
        issue_size = calculate_size_in_bytes(issue)
        if current_size + issue_size > max_size_bytes:
            batches.append({
                "projects": [
                    {
                        "name": project_details['name'],
                        "key": project_details['key'],
                        "type": project_details['projectTypeKey'],
                        "versions": project_details.get('versions', []),
                        "components": project_details.get('components', []),
                        "issues": current_batch
                    }
                ],
                "links": issue_links
            })
            current_batch = []
            current_size = 0
        current_batch.append(issue)
        current_size += issue_size

    if current_batch:
        batches.append({
            "projects": [
                {
                    "name": project_details['name'],
                    "key": project_details['key'],
                    "type": project_details['projectTypeKey'],
                    "versions": project_details.get('versions', []),
                    "components": project_details.get('components', []),
                    "issues": current_batch
                }
            ],
            "links": issue_links
        })

    return batches

def export_jira_issues(project_key):
    project_details = fetch_project_details(project_key)
    if not project_details:
        logging.error(f"Could not retrieve project details for {project_key}. Exiting...")
        return

    custom_fields = fetch_custom_fields()

    issues = fetch_issues(project_key)
    if not issues:
        logging.info("No issues found.")
        return

    logging.info(f"Total issues to export: {len(issues)}")

    user_cache = load_user_cache()

    mapped_issues, issue_links = map_issues_in_parallel(issues, custom_fields, user_cache)
    
    batches = split_issues_into_batches(mapped_issues, MAX_FILE_SIZE_BYTES, project_details, custom_fields, issue_links)

    for idx, batch in enumerate(batches, start=1):
        output_file = f"jira_export_{project_key}_batch_{idx}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(batch, f, ensure_ascii=False, indent=4)

        logging.info(f"File {output_file} created successfully with {len(batch['projects'][0]['issues'])} issues, size {calculate_size_in_bytes(batch)} bytes.")

if __name__ == "__main__":
    export_jira_issues(PROJECT_KEY)
