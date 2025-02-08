#!/usr/bin/env python3
import os
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional

def get_issue_node_id(owner: str, repo: str, issue_number: int, token: Optional[str] = None) -> Optional[str]:
    if not token: token = os.environ['GITHUB_TOKEN']
    query = '''
    query($owner: String!, $repo: String!, $number: Int!) {
      repository(owner: $owner, name: $repo) {
        issueOrPullRequest(number: $number) {
          ... on Issue { id }
          ... on PullRequest { id }
        }
      }
    }
    '''
    data = requests.post('https://api.github.com/graphql',
        json={'query': query, 'variables': {'owner': owner, 'repo': repo, 'number': issue_number}},
        headers={'Authorization': f'Bearer {token}'}).json()
    try:
        return data['data']['repository']['issueOrPullRequest']['id']
    except (KeyError, TypeError):
        print(f"Could not get node ID for {owner}/{repo}#{issue_number}")
        return None

def get_status_field(project_id: str, token: Optional[str] = None) -> Dict:
    if not token: token = os.environ['GITHUB_TOKEN']
    query = '''
    query($projectId: ID!) {
      node(id: $projectId) {
        ... on ProjectV2 {
          fields(first: 20) {
            nodes {
              ... on ProjectV2SingleSelectField {
                id
                name
                options {
                  id
                  name
                }
              }
            }
          }
        }
      }
    }
    '''
    data = requests.post('https://api.github.com/graphql',
        json={'query': query, 'variables': {'projectId': project_id}},
        headers={'Authorization': f'Bearer {token}'}).json()
    fields = data['data']['node']['fields']['nodes']
    return next((f for f in fields if f.get('options') is not None), None)

def get_project_items(project_id: str, token: Optional[str] = None) -> Dict:
    if not token: token = os.environ['GITHUB_TOKEN']
    query = '''
    query($projectId: ID!) {
      node(id: $projectId) {
        ... on ProjectV2 {
          items(first: 100) {
            nodes {
              id
              content {
                ... on Issue { id }
                ... on PullRequest { id }
              }
            }
          }
        }
      }
    }
    '''
    data = requests.post('https://api.github.com/graphql',
        json={'query': query, 'variables': {'projectId': project_id}},
        headers={'Authorization': f'Bearer {token}'}).json()
    try:
        return {item['content']['id']: item['id'] for item in data['data']['node']['items']['nodes'] if item.get('content')}
    except (KeyError, TypeError):
        print(f"Error getting project items: {data}")
        return {}

def add_issue_to_project(project_id: str, issue_node_id: str, token: Optional[str] = None) -> Optional[str]:
    if not token: token = os.environ['GITHUB_TOKEN']
    mutation = '''
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {
        projectId: $projectId
        contentId: $contentId
      }) {
        item {
          id
        }
      }
    }
    '''
    data = requests.post('https://api.github.com/graphql',
        json={'query': mutation, 'variables': {'projectId': project_id, 'contentId': issue_node_id}},
        headers={'Authorization': f'Bearer {token}'}).json()
    if 'errors' in data:
        print(f"Error adding issue: {data['errors']}")
        return None
    return data['data']['addProjectV2ItemById']['item']['id']

def add_todos_to_project(project_id: str, n_days: int = 30, token: Optional[str] = None) -> List[Dict]:
    if not token: token = os.environ['GITHUB_TOKEN']
    existing_items = get_project_items(project_id, token)
    
    # Get authenticated user
    user_query = 'query { viewer { login } }'
    user_data = requests.post('https://api.github.com/graphql',
        json={'query': user_query},
        headers={'Authorization': f'Bearer {token}'}).json()
    username = user_data['data']['viewer']['login']
    
    status_field = get_status_field(project_id)
    try:
        inbox_option = next(opt for opt in status_field['options'] if opt['name'] == 'Inbox')
    except (StopIteration, TypeError):
        print("Could not find Inbox status option")
        return []
    
    time_range = (datetime.now() - timedelta(days=n_days)).strftime('%Y-%m-%d')
    
    # Get watched repos
    watched_query = f'https://api.github.com/users/{username}/subscriptions'
    watched_repos = requests.get(watched_query, headers={'Authorization': f'Bearer {token}'}).json()
    watched_repo_names = [f"{repo['owner']['login']}/{repo['name']}" for repo in watched_repos]
    
    # Search issues
    search_url = 'https://api.github.com/search/issues'
    watched_q = f'repo:{" repo:".join(watched_repo_names)} state:open updated:>={time_range}' if watched_repo_names else None
    involved_q = f'involves:{username} state:open updated:>={time_range}'
    
    added_items = []
    for q in (watched_q, involved_q):
        if not q: continue
        issues = requests.get(f'{search_url}?q={q}', headers={'Authorization': f'Bearer {token}'}).json()
        
        for item in issues.get('items', []):
            try:
                owner = item['repository_url'].split('/')[-2]
                repo = item['repository_url'].split('/')[-1]
                issue_node_id = get_issue_node_id(owner, repo, item['number'], token)
                
                if not issue_node_id or issue_node_id in existing_items:
                    continue
                
                item_id = add_issue_to_project(project_id, issue_node_id)
                if item_id:
                    added_items.append({
                        'item_id': item_id,
                        'title': item['title'],
                        'url': item['html_url']
                    })
                    
                    update_status_mutation = '''
                    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
                        updateProjectV2ItemFieldValue(
                            input: {
                                projectId: $projectId
                                itemId: $itemId
                                fieldId: $fieldId
                                value: { singleSelectOptionId: $optionId }
                            }
                        ) {
                            projectV2Item { id }
                        }
                    }
                    '''
                    
                    data = requests.post('https://api.github.com/graphql',
                        json={'query': update_status_mutation,
                              'variables': {
                                  'projectId': project_id,
                                  'itemId': item_id,
                                  'fieldId': status_field['id'],
                                  'optionId': inbox_option['id']
                              }},
                        headers={'Authorization': f'Bearer {token}'}).json()
                    
                    if 'errors' in data:
                        print(f"Error setting status for {item['title']}: {data['errors']}")
            except Exception as e:
                print(f"Error processing item {item.get('title', 'Unknown')}: {e}")
                continue
                    
    return added_items

if __name__ == '__main__':
    project_id = 'PVT_kwHOAF93fM4Axts3'
    added = add_todos_to_project(project_id)
    print(f"Added {len(added)} new items to todo list")
