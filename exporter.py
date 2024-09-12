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

# Create a lock for synchronizing access to the user cache
file_lock = threading.Lock()

# Set of issues that are currently being processed
issues_in_progress = set()

# Function to load the user cache into memory to reduce file I/O operations
def load_user_cache():
    """Load the user cache into memory to reduce file I/O operations."""
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

# Function to save the user cache to a file
def save_user_cache(user_cache):
    """Write the entire user cache to the file to avoid frequent file writes."""
    with file_lock:
        with open(USER_CACHE_FILE, 'w') as file:
            for user_key, is_in_group in user_cache.items():
                file.write(f"{user_key},{is_in_group}\n")

# Safely cache the user group information
def cache_user_group(user_key, in_exempted_groups, user_cache):
    """Safely cache the user group information, ensuring the cache is updated only if necessary."""
    if user_key not in user_cache:
        user_cache[user_key] = str(in_exempted_groups)
        save_user_cache(user_cache)

# Check if the user is in an exempted group, and cache the result
def is_user_in_exempted_groups(user_key, user_cache):
    """Check if a user is in an exempted group and cache the result."""
    # First, check if the user is already cached
    if user_key in user_cache:
        return user_cache[user_key] == 'True'

    # If not cached, fetch group membership and cache it
    in_exempted_groups = fetch_user_group_membership(user_key)
    cache_user_group(user_key, in_exempted_groups, user_cache)
    return in_exempted_groups

# Function to fetch user group membership from Jira
def fetch_user_group_membership(user_key):
    """Fetch the group membership for a user from Jira."""
    url = f"{config['base_url']}/rest/api/2/user?accountId={user_key}&expand=groups"
    response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                            headers={"Accept": "application/json"})
    if response.status_code != 200:
        logging.error(f"Error fetching groups for user {user_key}: {response.status_code}")
        return False
    user_data = response.json()
    groups = [group['name'] for group in user_data['groups']['items']]
    return any(group in EXEMPTED_GROUPS for group in groups)

# Function to check if an issue is already processed
def is_issue_processed(issue_key):
    """Check if an issue has already been processed by reading from the cache file."""
    if os.path.exists(PROCESSED_ISSUES_CACHE):
        with open(PROCESSED_ISSUES_CACHE, 'r') as file:
            for line in file:
                if line.strip() == issue_key:
                    return True
    return False

# Function to mark an issue as processed
def mark_issue_as_processed(issue_key):
    """Mark an issue as processed by writing it to the cache file."""
    with open(PROCESSED_ISSUES_CACHE, 'a') as file:
        file.write(f"{issue_key}\n")

# Function to format Jira datetime values
def format_jira_datetime(value):
    """Format the Jira datetime string into a more readable format."""
    try:
        date_obj = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%f%z')
        return date_obj.strftime('%d/%b/%y %I:%M %p')
    except ValueError as e:
        logging.error(f"Error formatting date: {e}")
        return value

# Fetch a specific Jira issue by its key
def fetch_issue_by_key(issue_key):
    """Fetch a specific Jira issue by its key, including the changelog, comments, and issue links."""
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

# Fetch comments for a given issue
def fetch_issue_comments(issue_key):
    """Fetch comments for a specific issue."""
    url = f"{config['base_url']}/rest/api/2/issue/{issue_key}?fields=comment&expand=comment"
    response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                            headers={"Accept": "application/json"})
    if response.status_code != 200:
        logging.error(f"Error fetching comments for issue {issue_key}: {response.status_code}")
        return []
    issue_data = response.json()
    return issue_data['fields']['comment']['comments'] if 'comment' in issue_data['fields'] else []

# Handle user caching and fallback to a default author
def handle_user(user_key, user_cache):
    """Handle the user key and return a valid author or fallback to a default."""
    if is_user_in_exempted_groups(user_key, user_cache):
        return user_key
    return CUSTOM_COMMENT_AUTHOR

# Map the details of a single issue and ensure linked issues are processed
def map_issue_details(issue, custom_fields, mapped_issues, user_cache):
    """Maps the details of a single issue and ensures linked issues are processed."""
    issue_key = issue['key']

    # Check if the issue is already being processed or has been processed
    if issue_key in issues_in_progress:
        logging.info(f"Issue {issue_key} is already in progress. Skipping.")
        return None
    if is_issue_processed(issue_key):
        logging.info(f"Issue {issue_key} is already processed. Skipping.")
        return None

    # Mark the issue as "in progress"
    issues_in_progress.add(issue_key)

    mapped_issue = {
        "key": issue_key,
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
        "links": []  # To store linked issues
    }

    # Process issue links
    if 'issuelinks' in issue['fields'] and issue['fields']['issuelinks']:
        for link in issue['fields']['issuelinks']:
            link_type = link['type']['name']
            if 'inwardIssue' in link:
                linked_issue_key = link['inwardIssue']['key']
            elif 'outwardIssue' in link:
                linked_issue_key = link['outwardIssue']['key']
            else:
                continue

            # Add the linked issue to the current issue's links
            mapped_issue['links'].append({
                "linkType": link_type,
                "linkedIssueKey": linked_issue_key
            })

            # Process the linked issue if it has not been processed
            if not is_issue_processed(linked_issue_key) and linked_issue_key not in issues_in_progress:
                logging.info(f"Processing linked issue: {linked_issue_key}")
                linked_issue = fetch_issue_by_key(linked_issue_key)  # Fetch the full issue by key
                if linked_issue:
                    linked_mapped_issue = map_issue_details(linked_issue, custom_fields, mapped_issues, user_cache)  # Recursively process the linked issue
                    if linked_mapped_issue:
                        mapped_issues.append(linked_mapped_issue)  # Add the linked issue to the list of mapped issues
                    mark_issue_as_processed(linked_issue_key)  # Mark the linked issue as processed

    # Process custom fields
    for field_id, field_value in issue['fields'].items():
        if field_id.startswith("customfield_") and field_id in custom_fields and field_value:
            custom_field_info = custom_fields[field_id]
            value = field_value['value'] if isinstance(field_value, dict) and 'value' in field_value else field_value
            mapped_issue['customFieldValues'].append({
                "fieldName": custom_field_info['name'],
                "fieldType": custom_field_info.get('type', 'unknown'),
                "value": value
            })

    # Process attachments
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

    # Process comments
    comments = fetch_issue_comments(issue['key'])
    if comments:
        mapped_issue["comments"] = [
            {
                "body": c['body'],
                "author": handle_user(c['author']['accountId'], user_cache),
                "created": c['created']
            } for c in comments
        ]

    # Process changelog
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

    # Mark the current issue as processed
    mark_issue_as_processed(issue_key)

    # Remove the issue from the "in progress" set after processing
    issues_in_progress.remove(issue_key)

    logging.info(f"Issue {issue_key} mapped successfully.")
    return mapped_issue

# Function to fetch all custom fields in Jira
def fetch_custom_fields():
    """Fetch all custom fields in Jira and filter by allowed types."""
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

# Fetch all issues in a given project
def fetch_issues(project_key):
    """Fetch all issues in a given project by key."""
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
            'expand': 'changelog'
        }
        response = requests.get(url, auth=HTTPBasicAuth(config['email'], config['token']),
                                headers={"Accept": "application/json"}, params=params)
        data = response.json()
        issues.extend(data['issues'])
        total = data['total']
        start_at += max_results
        logging.info(f"Fetched {len(issues)} of {total} issues.")
    return issues

# Map issues using multithreading for better performance
def map_issues_in_parallel(issues, custom_fields, user_cache):
    """Map issues using multithreading for better performance and ensure linked issues are processed."""
    mapped_issues = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(map_issue_details, issue, custom_fields, mapped_issues, user_cache): issue for issue in issues}
        for future in as_completed(futures):
            mapped_issue = future.result()
            if mapped_issue:
                mapped_issues.append(mapped_issue)

    return mapped_issues

# Main function to export issues from Jira, including linked issues, to JSON files
def export_jira_issues(project_key):
    """Main function to export issues from Jira, including linked issues, to JSON files."""
    custom_fields = fetch_custom_fields()
    issues = fetch_issues(project_key)
    if not issues:
        logging.info("No issues found.")
        return

    logging.info(f"Total issues to export: {len(issues)}")

    # Load the user cache into memory
    user_cache = load_user_cache()

    # Process the issues in parallel
    mapped_issues = map_issues_in_parallel(issues, custom_fields, user_cache)

    # Output the results to a JSON file
    output_file = f"jira_export_{project_key}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(mapped_issues, f, ensure_ascii=False, indent=4)

    logging.info(f"Export completed: {output_file}")

if __name__ == "__main__":
    export_jira_issues(PROJECT_KEY)
