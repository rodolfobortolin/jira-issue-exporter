
# Jira Issue Exporter with Linked Issues and Custom Fields

## Overview

This script is designed to export Jira issues from both **Jira Cloud** and **Jira Data Center**. It handles large exports by splitting them into files that are less than 7 MB in size, making it ideal for exporting large Jira projects. The script also manages user data, custom fields, comments, attachments, and linked issues using unique IDs.

## Features

- Supports both **Jira Cloud** and **Jira Data Center**.
- The script exports all issue information (custom fields, attachments, links, comments, histories, versions, components).
- The script maintains a cache of users that we do not want to anonymize.
- The script keeps a cache of account IDs to avoid fetching them via REST every time (for both Data Center and Cloud).
- The script creates issues in parallel, significantly reducing the export time.
- The script manages which users can or cannot be exported to the JSON based on their groups: if they are not in group X, it assigns a default user; otherwise, it attempts to get the account ID of the user in Cloud to include in the JSON.
- The script stores all processed issues (imported into the JSON) to prevent duplicate issues.
- The script fetches all issues from the project in parallel, saving a lot of time.
- Due to a limitation in Cloud imports, the script manages and avoids creating files larger than 7MB, breaking them into batches.
- The script prioritizes linked issues immediately after detection to keep them in the same batch, preventing linked issues from being split and losing the link between them.

## Setup and Requirements

1. Python 3.x
2. Install dependencies:
    ```bash
    pip install requests
    ```

3. Make sure you configure Jira credentials for **Jira Cloud** or **Jira Data Center** in the script.

## How to Use

1. Clone the repository:
    ```bash
    git clone https://github.com/yourusername/jira-issue-exporter.git
    ```

2. Run the script:
    ```bash
    python export_jira_issues.py
    ```

3. You will be prompted to choose between **Jira Cloud** or **Jira Data Center** and to enter the Jira project key you want to export.

4. The script will export the issues and split them into JSON files named `jira_export_<project_key>_batch_<index>.json`.

## Configuration

### Jira Cloud Configuration

In the script, set your **email**, **API token**, and **base URL** for Jira Cloud:
```python
cloud_config = {
    'email': 'your_email@domain.com',
    'token': 'your_api_token',
    'base_url': 'https://yourdomain.atlassian.net',
}
```

### Jira Data Center Configuration

For Jira Data Center, set your **username**, **password**, and **base URL**:
```python
config = {
    'username': 'admin', 
    'password': 'admin',  
    'base_url': 'http://localhost:8080',
    'auth_type': 'basic'
}
```

## Key Functionalities

### Exporting Issues

The script fetches issues, their details (including custom fields, comments, attachments, and linked issues), and splits the data into multiple JSON files if the total size exceeds the defined limit (7 MB).

### Linked Issues

Linked issues are mapped using unique IDs, rather than Jira issue keys. The script ensures that each link between issues is included in the export, even when those issues are in separate files.

### Custom Fields

The script processes custom fields and includes them in the exported data. It supports various custom field types, including text, date, user picker, and others.

### User Management

The script manages user data (such as group membership) and stores this information in a local cache to avoid repeated API calls for the same users. It checks if users belong to specific groups and maps their data accordingly.

## File Structure

- **`users_cache.txt`**: Caches user group information to minimize API calls.
- **`users_accounts.txt`**: Stores user email-to-account ID mappings.
- **`processed_issues_cache.txt`**: Tracks which issues have already been processed.

## Logging

The script logs its operations, including any errors encountered while fetching data, to the console for easy debugging and monitoring.

## Example Output

Once the script completes, it will generate JSON files, such as:

```json
{
    "projects": [
        {
            "name": "My Project",
            "key": "MYPROJ",
            "type": "software",
            "issues": [
                {
                    "key": "MYPROJ-1",
                    "summary": "Issue Summary",
                    "description": "Issue Description",
                    "priority": "High",
                    "customFieldValues": [
                        {
                            "fieldName": "My Custom Field",
                            "value": "Some value"
                        }
                    ],
                    "comments": [
                        {
                            "body": "This is a comment",
                            "author": "user@example.com",
                            "created": "2024-09-12T12:34:56.789+0000"
                        }
                    ]
                }
            ]
        }
    ],
    "links": [
        {
            "name": "Blocks",
            "sourceId": "1",
            "destinationId": "2"
        }
    ]
}
```
