
# Jira Exporter Script

## Overview

This script is designed to export issues from a specified Jira project, handling both Jira Cloud and Jira Data Center versions. It interacts with the Jira API to fetch issues, users, custom fields, and other related data, and then exports this information into JSON files suitable for migration or backup purposes.

## Features

- **Supports Both Jira Versions**: Choose between Jira Cloud and Jira Data Center during runtime.
- **Authentication Handling**: Supports both token-based and basic authentication.
- **User Data Management**:
  - Caches user data to avoid redundant API calls.
  - Handles user privacy by replacing non-exempted users with a custom user ID.
- **Custom Fields Support**:
  - Fetches and processes allowed custom fields.
  - Handles various custom field types, including text, date, user pickers, etc.
- **Issue Linking**:
  - Processes issue links and ensures linked issues are also exported.
- **Attachments and Comments**:
  - Exports attachments with metadata.
  - Includes comments with author handling.
- **History Export**:
  - Captures the change history of issues.
- **Batch Exporting**:
  - Splits the export into multiple JSON files if the data exceeds a specified size limit.
- **Threaded Processing**:
  - Utilizes threading to process multiple issues concurrently.

## Setup

### Prerequisites

- Python 3.6 or higher.
- Required Python packages:
  - `requests`
  - `logging`
  - `json`
  - `threading`
  - `concurrent.futures`

Install the required packages using:

```bash
pip install requests
```

### Configuration

#### Jira Cloud Configuration

Update the `cloud_config` dictionary in the `main` function with your Jira Cloud credentials:

```python
cloud_config = {
    'email': 'your-email@example.com',
    'token': 'your-api-token',
    'base_url': "https://your-domain.atlassian.net",
    'auth_type': 'token'
}
```

- **email**: Your Jira Cloud account email.
- **token**: Your Jira API token. [Get an API token](https://confluence.atlassian.com/cloud/api-tokens-938839638.html).
- **base_url**: Your Jira Cloud base URL.
- **auth_type**: Set to `'token'` for Jira Cloud.

#### Jira Data Center Configuration

Update the `config` dictionary with your Jira Data Center credentials if you select Jira Data Center:

```python
config = {
    'username': 'your-username',
    'password': 'your-password',
    'base_url': "http://your-jira-instance.com",
    'auth_type': 'basic'
}
```

- **username**: Your Jira Data Center username.
- **password**: Your Jira Data Center password.
- **base_url**: Your Jira Data Center base URL.
- **auth_type**: Set to `'basic'` for Jira Data Center.

## Usage

1. **Run the Script**:

   ```bash
   python your_script_name.py
   ```

2. **Select Jira Version**:

   When prompted, select:

   - `1` for Jira Cloud
   - `2` for Jira Data Center

3. **Enter Project Key**:

   Input the key of the Jira project you wish to export.

## Output

- The script will generate one or more JSON files named in the format:

  ```
  jira_export_{PROJECT_KEY}_batch_{BATCH_NUMBER}.json
  ```

- Each file contains:

  - Project details.
  - Issues with all related data (attachments, comments, history, custom fields).
  - Issue links.

## Customization

- **Max File Size**:

  Adjust the `MAX_FILE_SIZE_MB` constant in the `JiraExporter` class to change the maximum size of each output file.

  ```python
  MAX_FILE_SIZE_MB = 7  # Default is 7 MB
  ```

- **Exempted Groups**:

  Modify the `EXEMPTED_GROUPS` list to include any user groups whose members should not be anonymized.

  ```python
  EXEMPTED_GROUPS = ["jira-administrators", "your-custom-group"]
  ```

- **Allowed Custom Field Types**:

  Update the `ALLOWED_CUSTOM_FIELD_TYPES` list to control which custom field types are processed.

## Caching

- **User Cache**:

  - Stored in `users_cache.txt`.
  - Keeps track of users and whether they belong to exempted groups.

- **User Accounts**:

  - Stored in `users_accounts.txt`.
  - Maps user emails to account IDs (used when mapping users from Data Center to Cloud).

- **Processed Issues Cache**:

  - Stored in `processed_issues_cache.txt`.
  - Keeps track of issues that have already been processed to avoid duplication.

## Thread Safety

- The script uses threading locks when reading from or writing to cache files to ensure thread safety.

## Error Handling

- The script logs errors encountered during API calls and data processing.
- If an error occurs while formatting dates or fetching data, it logs the error and continues processing.

## Logging

- Logs are printed to the console with timestamps and log levels.
- You can adjust the logging level by modifying the `basicConfig` call:

  ```python
  logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s: %(message)s')
  ```

## Limitations

- **Attachments**: The script only stores the URI of attachments, not the actual files.
- **API Rate Limits**: Be mindful of Jira API rate limits, especially for large projects.
- **User Privacy**: Users not in exempted groups are replaced with a custom user ID to maintain privacy.
