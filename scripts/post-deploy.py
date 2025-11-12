#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from pathlib import Path

def main():
    # Get stack name from command line args or environment
    stack_name = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('STACK_NAME')

    if not stack_name:
        print("❌ Stack name is required")
        print("Usage: python3 post-deploy.py <stack-name>")
        sys.exit(1)

    print("🚀 Running post-deployment tasks...")

    try:
        generate_aws_exports(stack_name)
        print("✅ Post-deployment tasks completed successfully")
    except Exception as error:
        print(f"❌ Post-deployment tasks failed: {error}")
        sys.exit(1)

def generate_aws_exports(stack_name):
    print(f"🔍 Fetching configuration from CDK stack: {stack_name}")

    try:
        # Get stack information using AWS CLI
        command = [
            "aws", "cloudformation", "describe-stacks",
            "--stack-name", stack_name,
            "--output", "json"
        ]

        result = subprocess.run(command, capture_output=True, text=True, check=True)
        stack_data = json.loads(result.stdout)

        # Extract stack info
        stack = stack_data['Stacks'][0]
        outputs = stack.get('Outputs', [])

        # Get region from stack ARN
        # Stack ARN format: arn:aws:cloudformation:region:account:stack/stack-name/stack-id
        stack_arn = stack['StackId']
        region = stack_arn.split(':')[3]

        # Convert outputs to key-value pairs
        output_map = {}
        for output in outputs:
            output_map[output['OutputKey']] = output['OutputValue']

        # Validate required outputs
        required = ["CognitoClientId", "CognitoUserPoolId", "AmplifyUrl", "RuntimeArn", "FeedbackApiUrl"]
        missing = [key for key in required if key not in output_map]

        if missing:
            raise Exception(f"Missing required stack outputs: {', '.join(missing)}")

        # Generate aws-exports.json content with correct Cognito IDP authority
        aws_exports = {
            "authority": f"https://cognito-idp.{region}.amazonaws.com/{output_map['CognitoUserPoolId']}",
            "client_id": output_map['CognitoClientId'],
            "redirect_uri": output_map['AmplifyUrl'],
            "post_logout_redirect_uri": output_map['AmplifyUrl'],
            "response_type": "code",
            "scope": "email openid profile",
            "automaticSilentRenew": True,
            "agentRuntimeArn": output_map['RuntimeArn'],
            "awsRegion": region,
            "feedbackApiUrl": output_map['FeedbackApiUrl'],
        }

        # Write to frontend/public directory
        script_dir = Path(__file__).parent
        frontend_dir = script_dir.parent / "frontend"
        public_dir = frontend_dir / "public"

        # Ensure public directory exists
        public_dir.mkdir(parents=True, exist_ok=True)

        output_path = public_dir / "aws-exports.json"
        with open(output_path, 'w') as f:
            json.dump(aws_exports, f, indent=2)

        print("✅ Generated aws-exports.json successfully")
        print(f"📁 File location: {output_path}")
        print("📋 Configuration:")
        print(f"   Authority: {aws_exports['authority']}")
        print(f"   Client ID: {aws_exports['client_id']}")
        print(f"   Redirect URI: {aws_exports['redirect_uri']}")
        print(f"   Feedback API URL: {aws_exports['feedbackApiUrl']}")

    except subprocess.CalledProcessError as error:
        raise Exception(f"AWS CLI command failed: {error}")
    except json.JSONDecodeError as error:
        raise Exception(f"Failed to parse AWS CLI output: {error}")
    except Exception as error:
        raise Exception(f"Error generating aws-exports.json: {error}")

if __name__ == "__main__":
    main()
