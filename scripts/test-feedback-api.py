#!/usr/bin/env python3
"""
Test script for Feedback API
"""

import sys
import json
import getpass
from pathlib import Path
from typing import Dict, Optional, Tuple
import requests
from colorama import Fore, Style

# Add scripts directory to path for reliable imports
script_dir = Path(__file__).parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

# Import shared utilities
from utils import (
    get_stack_config,
    get_ssm_params,
    authenticate_cognito,
    print_msg,
    print_section,
)


def make_api_request(
    url: str, token: str, method: str = "POST", data: Optional[Dict] = None
) -> Tuple[int, Dict]:
    """Make an authenticated API request and return status code and response body."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    try:
        if method == "POST":
            response = requests.post(url, headers=headers, json=data)
        elif method == "GET":
            response = requests.get(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        return response.status_code, response.json()
    except requests.exceptions.RequestException as e:
        print_msg(f"Request failed: {e}", "error")
        return 0, {}


def test_positive_feedback(api_url: str, token: str) -> bool:
    """Test sending positive feedback."""
    print("Test 1: Sending positive feedback...")
    
    status_code, body = make_api_request(
        f"{api_url}/feedback",
        token,
        method="POST",
        data={
            "sessionId": "test-session-123",
            "message": "foo",  # Agent's response
            "feedbackType": "positive",
            "comment": "bar",  # Optional: User's feedback comment
        },
    )
    
    if status_code == 200:
        print(f"{Fore.GREEN}✓ Test 1 passed (HTTP {status_code}){Style.RESET_ALL}")
        print(f"  Response: {json.dumps(body, indent=2)}")
        return True
    else:
        print(f"{Fore.RED}✗ Test 1 failed (HTTP {status_code}){Style.RESET_ALL}")
        print(f"  Response: {json.dumps(body, indent=2)}")
        return False


def test_negative_feedback(api_url: str, token: str) -> bool:
    """Test sending negative feedback."""
    print("\nTest 2: Sending negative feedback...")
    
    status_code, body = make_api_request(
        f"{api_url}/feedback",
        token,
        method="POST",
        data={
            "sessionId": "test-session-456",
            "message": "foo",  # Agent's response
            "feedbackType": "negative",
            "comment": "bar",  # Optional: User's feedback comment
        },
    )
    
    if status_code == 200:
        print(f"{Fore.GREEN}✓ Test 2 passed (HTTP {status_code}){Style.RESET_ALL}")
        print(f"  Response: {json.dumps(body, indent=2)}")
        return True
    else:
        print(f"{Fore.RED}✗ Test 2 failed (HTTP {status_code}){Style.RESET_ALL}")
        print(f"  Response: {json.dumps(body, indent=2)}")
        return False


def test_missing_field(api_url: str, token: str) -> bool:
    """Test that missing required fields are properly rejected."""
    print("\nTest 3: Testing missing required field (should fail with 400)...")
    
    status_code, body = make_api_request(
        f"{api_url}/feedback",
        token,
        method="POST",
        data={"sessionId": "test-session-999"},  # Missing message and feedbackType
    )
    
    if status_code == 400:
        print(f"{Fore.GREEN}✓ Test 3 passed (HTTP {status_code} - correctly rejected missing fields){Style.RESET_ALL}")
        print(f"  Response: {json.dumps(body, indent=2)}")
        return True
    else:
        print(f"{Fore.RED}✗ Test 3 failed (HTTP {status_code} - should have been 400){Style.RESET_ALL}")
        print(f"  Response: {json.dumps(body, indent=2)}")
        return False


def run_tests(api_url: str, token: str) -> Tuple[int, int]:
    """
    Run all tests and return (passed, failed) counts.
    
    Field semantics for feedback API:
    - sessionId: The conversation session identifier
    - message: The AGENT'S RESPONSE that is receiving feedback (what the AI said)
    - feedbackType: Either 'positive' or 'negative'
    - comment (optional): User's explanation for their feedback rating
    
    To add new tests:
    1. Create a new test function following the pattern above
    2. Add it to the tests list below
    """
    print_section("Running Tests", width=42)
    
    # Add new tests to this list
    tests = [
        test_positive_feedback,
        test_negative_feedback,
        test_missing_field,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        if test_func(api_url, token):
            passed += 1
        else:
            failed += 1
    
    return passed, failed


def main():
    """Main entry point."""
    print("=" * 42)
    print("Feedback API Test Script")
    print("=" * 42 + "\n")
    
    # Load configuration
    config = get_stack_config()
    print(f"Using stack: {config['stack_name']}\n")
    
    # Fetch SSM params
    print("Fetching configuration from SSM...")
    params = get_ssm_params(
        config['stack_name'],
        'cognito-user-pool-id',
        'cognito-user-pool-client-id',
        'feedback-api-url'
    )
    print_msg("Configuration fetched successfully")
    print(f"  User Pool ID: {params['cognito-user-pool-id']}")
    print(f"  Client ID: {params['cognito-user-pool-client-id']}")
    print(f"  API URL: {params['feedback-api-url']}")
    
    # Get credentials
    print_section("Authentication", width=42)
    
    username = input("Enter username: ").strip() or "testuser"
    password = getpass.getpass(f"Enter password for {username}: ")
    
    # Authenticate
    access_token, _user_id = authenticate_cognito(
        params['cognito-user-pool-id'],
        params['cognito-user-pool-client-id'],
        username,
        password
    )
    
    # Run tests
    passed, failed = run_tests(params['feedback-api-url'], access_token)
    
    # Summary
    print("\n" + "=" * 42)
    print("Test Summary")
    print("=" * 42)
    print(f"Passed: {Fore.GREEN}{passed}{Style.RESET_ALL}")
    print(f"Failed: {Fore.RED}{failed}{Style.RESET_ALL}\n")
    
    # NOTE: Table name follows the pattern '{stack_name}-feedback' defined in infra-cdk/lib/backend-stack.ts
    # If you change the table name in CDK, update this reference accordingly.
    print(f"To view the stored feedback, open the DynamoDB table named '{config['stack_name']}-feedback' in the AWS Console.\n")
    
    if failed == 0:
        print(f"{Fore.GREEN}All tests passed! ✓{Style.RESET_ALL}")
        sys.exit(0)
    else:
        print(f"{Fore.RED}Some tests failed.{Style.RESET_ALL}")
        sys.exit(1)


if __name__ == "__main__":
    main()
