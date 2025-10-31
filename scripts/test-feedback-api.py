#!/usr/bin/env python3
"""
Test script for Feedback API
"""

import sys
import json
import getpass
from pathlib import Path
from typing import Dict, Optional, Tuple
import boto3
import requests
import yaml
from colorama import Fore, Style, init

# Initialize colorama for cross-platform colored output
init(autoreset=True)

def load_stack_name() -> str:
    """Load stack name from config.yaml."""
    # Get the script's directory and navigate to config.yaml
    script_dir = Path(__file__).parent
    config_path = script_dir.parent / "infra-cdk" / "config.yaml"
    
    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found at: {config_path}\n"
            f"Make sure you're running from the project directory."
        )
    
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    
    if "stack_name_base" not in config:
        raise KeyError(
            f"'stack_name_base' not found in {config_path}\n"
            f"Please add 'stack_name_base' to your config.yaml"
        )
    
    return config["stack_name_base"]


def fetch_config_from_ssm(stack_name: str) -> Dict[str, str]:
    """Fetch configuration from AWS SSM Parameter Store."""
    print("Fetching configuration from SSM...")
    
    ssm = boto3.client("ssm")
    
    try:
        user_pool_id = ssm.get_parameter(
            Name=f"/{stack_name}/cognito-user-pool-id"
        )["Parameter"]["Value"]
        
        client_id = ssm.get_parameter(
            Name=f"/{stack_name}/cognito-user-pool-client-id"
        )["Parameter"]["Value"]
        
        api_url = ssm.get_parameter(
            Name=f"/{stack_name}/feedback-api-url"
        )["Parameter"]["Value"]
        
        print(f"{Fore.GREEN}✓ Configuration fetched successfully{Style.RESET_ALL}")
        print(f"  User Pool ID: {user_pool_id}")
        print(f"  Client ID: {client_id}")
        print(f"  API URL: {api_url}")
        
        return {
            "user_pool_id": user_pool_id,
            "client_id": client_id,
            "api_url": api_url.rstrip("/"),
        }
    except Exception as e:
        print(f"{Fore.RED}Error: Could not fetch configuration from SSM.{Style.RESET_ALL}")
        print("Make sure the stack is deployed and you have AWS credentials configured.")
        print(f"Details: {e}")
        sys.exit(1)


def authenticate(user_pool_id: str, client_id: str, username: str, password: str) -> str:
    """Authenticate with Cognito and return ID token."""
    print("\nAuthenticating...")
    
    cognito = boto3.client("cognito-idp")
    
    try:
        # Check if user exists
        try:
            cognito.admin_get_user(UserPoolId=user_pool_id, Username=username)
        except cognito.exceptions.UserNotFoundException:
            print(f"{Fore.RED}User '{username}' does not exist. Exiting.{Style.RESET_ALL}")
            sys.exit(1)
        
        # Authenticate
        response = cognito.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            ClientId=client_id,
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
        
        token = response["AuthenticationResult"]["IdToken"]
        print(f"{Fore.GREEN}✓ Authentication successful{Style.RESET_ALL}")
        return token
        
    except Exception as e:
        print(f"{Fore.RED}Authentication failed: {e}{Style.RESET_ALL}")
        sys.exit(1)


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
        print(f"{Fore.RED}Request failed: {e}{Style.RESET_ALL}")
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
            "message": "Amazon S3 provides object storage with high durability and availability. It offers features like versioning, lifecycle policies, and encryption.",  # Agent's response
            "feedbackType": "positive",
            "comment": "Very helpful and accurate explanation of S3 features",  # Optional: User's feedback comment
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
            "message": "AWS Lambda is a serverless compute service.",  # Agent's response
            "feedbackType": "negative",
            "comment": "Too brief, didn't answer my pricing question",  # Optional: User's feedback comment
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
    print("\n" + "=" * 42)
    print("Running Tests")
    print("=" * 42 + "\n")
    
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
    stack_name = load_stack_name()
    print(f"Using stack: {stack_name}\n")
    
    config = fetch_config_from_ssm(stack_name)
    
    # Get credentials
    print("\n" + "=" * 42)
    print("Authentication")
    print("=" * 42 + "\n")
    
    username = input("Enter username: ").strip() or "testuser"
    password = getpass.getpass(f"Enter password for {username}: ")
    
    # Authenticate
    token = authenticate(config["user_pool_id"], config["client_id"], username, password)
    
    # Run tests
    passed, failed = run_tests(config["api_url"], token)
    
    # Summary
    print("\n" + "=" * 42)
    print("Test Summary")
    print("=" * 42)
    print(f"Passed: {Fore.GREEN}{passed}{Style.RESET_ALL}")
    print(f"Failed: {Fore.RED}{failed}{Style.RESET_ALL}\n")
    
    if failed == 0:
        print(f"{Fore.GREEN}All tests passed! ✓{Style.RESET_ALL}")
        sys.exit(0)
    else:
        print(f"{Fore.RED}Some tests failed.{Style.RESET_ALL}")
        sys.exit(1)


if __name__ == "__main__":
    main()
