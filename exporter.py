import requests
import logging
import json
import os
from requests.auth import HTTPBasicAuth
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s: %(message)s')

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

def format_jira_datetime(value):
    try:
        date_obj = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%f%z')
        return date_obj.strftime('%d/%b/%y %I:%M %p')
    except ValueError as e:
        logging.error(f"Error formatting date: {e}")
        return value

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

def cache_user_group(user_key, in_exempted_groups):
    with open(USER_CACHE_FILE, 'a') as file:
        file.write(f"{user_key},{in_exempted_groups}\n")

def is_user_in_exempted_groups(user_key):
    if os.path.exists(USER_CACHE_FILE):
        with open(USER_CACHE_FILE, 'r') as file:
            for line in file:
                try:
                    cached_user, is_in_group = line.strip().split(',', 1)
                    if cached_user == user_key:
                        return is_in_group == 'True'
                except ValueError:
                    logging.warning(f"Skipping malformed line in cache file: {line.strip()}")
                    continue
    in_exempted_groups = fetch_user_group_membership(user_key)
    cache_user_group(user_key, in_exempted_groups)
    return in_exempted_groups

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

def fetch_project_details(project_key):
    url = f"{config['base_url']}/rest/api/2/project/{project_key}"
    response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                            headers={"Accept": "application/json"})
    if response.status_code != 200:
        logging.error(f"Error fetching project details for {project_key}: {response.status_code}")
        return None
    project_data = response.json()
    
    # Map the project details
    mapped_project = {
        "name": project_data['name'],
        "key": project_data['key'],
        "versions": [
            {
                "name": version['name'],
                "released": version.get('released', False),
                "releaseDate": version.get('releaseDate')  # Can be None
            } for version in project_data.get('versions', [])
        ],
        "components": [
            component['name'] for component in project_data.get('components', [])
        ]
    }
    logging.info(f"Project details for {project_key} retrieved successfully.")
    return mapped_project

def fetch_issues(project_key):
    issues = []
    start_at = 0
    max_results = 100
    total = 1
    while start_at < total:
        url = f"{config['base_url']}/rest/api/2/search"
        params = {
            'jql': f'project={project_key} order by key desc',
            'startAt': start_at,
            'maxResults': max_results,
            'expand': 'changelog',
            'fields': '*all'
        }
        response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                                headers={"Accept": "application/json"}, params=params)
        data = response.json()
        issues.extend(data['issues'])
        total = data['total']
        start_at += max_results
        logging.info(f"Fetched {len(issues)} of {total} issues.")
    return issues

def fetch_issue_comments(issue_key):
    url = f"{config['base_url']}/rest/api/2/issue/{issue_key}?fields=comment&expand=comment"
    response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                            headers={"Accept": "application/json"})
    if response.status_code != 200:
        logging.error(f"Error fetching comments for issue {issue_key}: {response.status_code}")
        return []
    issue_data = response.json()
    return issue_data['fields']['comment']['comments'] if 'comment' in issue_data['fields'] else []

def handle_user(user_key):
    if is_user_in_exempted_groups(user_key):
        return user_key
    return CUSTOM_COMMENT_AUTHOR

def map_issue_details(issue, custom_fields):
    """Maps the details of a single issue."""
    mapped_issue = {
        "priority": issue['fields']['priority']['name'] if issue['fields'].get('priority') else None,
        "description": issue['fields'].get('description', ''),
        "status": issue['fields']['status']['name'],
        "reporter": handle_user(issue['fields']['reporter']['accountId']) if issue['fields'].get('reporter') else CUSTOM_COMMENT_AUTHOR,
        "labels": issue['fields'].get('labels', []),
        "issueType": issue['fields']['issuetype']['name'],
        "resolution": issue['fields']['resolution']['name'] if issue['fields'].get('resolution') else None,
        "created": issue['fields']['created'],
        "updated": issue['fields']['updated'],
        "resolutiondate": issue['fields'].get('resolutiondate'),
        "duedate": issue['fields'].get('duedate'),
        "affectedVersions": [v['name'] for v in issue['fields'].get('versions', [])],
        "summary": issue['fields']['summary'],
        "assignee": handle_user(issue['fields']['assignee']['accountId']) if issue['fields'].get('assignee') else CUSTOM_COMMENT_AUTHOR,
        "fixedVersions": [v['name'] for v in issue['fields'].get('fixVersions', [])],
        "components": [c['name'] for c in issue['fields'].get('components', [])],
        "customFieldValues": []
    }

    # Process issue links
    if 'issuelinks' in issue['fields'] and issue['fields']['issuelinks']:
        # Initialize 'links' if it doesn't exist
        if 'links' not in mapped_issue:
            mapped_issue['links'] = []
            
        for link in issue['fields']['issuelinks']:
            link_type = link['type']['name']
            if 'inwardIssue' in link:
                mapped_issue['links'].append({
                    "name": link_type,
                    "sourceId": link['inwardIssue']['key'],
                    "destinationId": issue['key']
                })
            elif 'outwardIssue' in link:
                mapped_issue['links'].append({
                    "name": link_type,
                    "sourceId": issue['key'],
                    "destinationId": link['outwardIssue']['key']
                })

    for field_id, field_value in issue['fields'].items():
        if field_id.startswith("customfield_") and field_id in custom_fields and field_value:
            custom_field_info = custom_fields[field_id]
            if isinstance(field_value, dict) and 'value' in field_value:
                value = field_value['value']
            elif isinstance(field_value, list):
                value = [item['value'] if isinstance(item, dict) and 'value' in item else item for item in field_value]
            else:
                value = field_value
            if custom_field_info['type'] == 'com.atlassian.jira.plugin.system.customfieldtypes:datetime':
                value = format_jira_datetime(value)
            if custom_field_info['type'] in ['com.atlassian.jira.plugin.system.customfieldtypes:userpicker', 
                                             'com.atlassian.jira.plugin.system.customfieldtypes:multiuserpicker']:
                if custom_field_info['type'] == 'com.atlassian.jira.plugin.system.customfieldtypes:userpicker':
                    value = handle_user(value['accountId'] if isinstance(value, dict) else value)
                elif custom_field_info['type'] == 'com.atlassian.jira.plugin.system.customfieldtypes:multiuserpicker':
                    value = [handle_user(user['accountId']) for user in field_value if isinstance(user, dict)]
            mapped_issue['customFieldValues'].append({
                "fieldName": custom_field_info['name'],
                "fieldType": custom_field_info.get('type', 'unknown'),
                "value": value
            })

    if 'attachment' in issue['fields'] and issue['fields']['attachment']:
        mapped_issue["attachments"] = [
            {
                "name": a['filename'],
                "attacher": handle_user(a['author']['accountId']),
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
                "author": handle_user(c['author']['accountId']),
                "created": c['created']
            } for c in comments
        ]

    if 'changelog' in issue and issue['changelog'].get('histories'):
        mapped_issue["history"] = [
            {
                "author": handle_user(h['author']['accountId']) if h.get('author') else CUSTOM_COMMENT_AUTHOR,
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

    logging.info(f"Issue {issue['key']} mapped successfully.")
    return mapped_issue

def calculate_size_in_bytes(data):
    return len(json.dumps(data).encode('utf-8'))

def split_issues_into_batches(mapped_issues, max_size_bytes, project_details):
    """Divide as issues mapeadas em batches sem reprocessá-las."""
    batches = []
    current_batch = []
    current_size = 0

    # Iterar sobre as issues já mapeadas
    for mapped_issue in mapped_issues:
        issue_size = calculate_size_in_bytes(mapped_issue)
        if current_size + issue_size > max_size_bytes:
            batches.append({
                "projects": [
                    {
                        "name": project_details['name'],
                        "key": project_details['key'],
                        "versions": project_details['versions'],
                        "components": project_details['components'],
                        "issues": current_batch
                    }
                ]
            })
            current_batch = []
            current_size = 0
        current_batch.append(mapped_issue)
        current_size += issue_size

    # Adiciona o último batch
    if current_batch:
        batches.append({
            "projects": [
                {
                    "name": project_details['name'],
                    "key": project_details['key'],
                    "versions": project_details['versions'],
                    "components": project_details['components'],
                    "issues": current_batch
                }
            ]
        })

    return batches

def map_issues_in_parallel(issues, custom_fields):
    """Map issues using multithreading for better performance."""
    mapped_issues = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(map_issue_details, issue, custom_fields): issue for issue in issues}
        for future in as_completed(futures):
            mapped_issue = future.result()
            mapped_issues.append(mapped_issue)
    
    return mapped_issues

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

    # Process the issues in parallel
    mapped_issues = map_issues_in_parallel(issues, custom_fields)

    # Split into batches and write to files
    batches = split_issues_into_batches(mapped_issues, MAX_FILE_SIZE_BYTES, project_details)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(write_batch_to_file, project_key, idx, batch): batch for idx, batch in enumerate(batches, start=1)}
        for future in as_completed(futures):
            future.result()

def write_batch_to_file(project_key, idx, batch):
    output_file = f"jira_export_{project_key}_batch_{idx}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(batch, f, ensure_ascii=False, indent=4)

    logging.info(f"File {output_file} created successfully with {len(batch['projects'][0]['issues'])} issues, size {calculate_size_in_bytes(batch)} bytes.")

if __name__ == "__main__":
    export_jira_issues(PROJECT_KEY)
