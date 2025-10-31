"""
Feedback API Lambda Handler

CORS CONFIGURATION TODO:

THE ISSUE - Bidirectional Dependency:
- Frontend needs from Backend: Runtime ARN, Cognito config (via SSM)
- Backend needs from Frontend: CloudFront URL (via frontendStack.distribution.distributionDomainName)

CURRENT STATE:
- ALLOWED_ORIGINS='*' allows any origin (Cognito JWT still protects endpoints)
- This Lambda's ALLOWED_ORIGINS is the PRIMARY CORS control (API Gateway only handles OPTIONS)

OPTIONS TO FIX:

1. Custom Resource Post-Deployment Update:
   - Custom resource = Lambda-backed CloudFormation resource that runs custom logic during stack lifecycle
   - Flow: Frontend deploys → writes CloudFront URL to SSM → custom resource Lambda triggers
   - Custom resource reads SSM, updates this Lambda's ALLOWED_ORIGINS via AWS SDK
   - Pros: Automated, single deployment | Cons: Complex (~150 lines Lambda code)

2. Two-Phase Deployment (Separate Stacks):
   - Phase 1: Deploy Backend + Frontend independently
   - Phase 2: Update Backend with CloudFront URL, redeploy
   - Pros: Clean separation | Cons: Two deployment commands

3. Accept Current State:
   - Keep wildcard, rely on Cognito JWT as primary security
   - Pros: No changes needed | Cons: Not security best practice

See: 
- https://docs.aws.amazon.com/apigateway/latest/developerguide/how-to-cors.html
- https://docs.aws.amazon.com/cdk/v2/guide/resources.html#resources-referencing
"""

import json
import os
import re
import time
import uuid
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

# Initialize DynamoDB client
dynamodb = boto3.client('dynamodb')

# Environment variables
TABLE_NAME = os.environ['TABLE_NAME']
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '')

# Validation constants
MAX_SESSION_ID_LENGTH = 100
MAX_MESSAGE_LENGTH = 5000
SESSION_ID_PATTERN = re.compile(r'^[a-zA-Z0-9-_]+$')


def get_cors_headers() -> Dict[str, str]:
    """Return CORS headers for API responses."""
    return {
        'Access-Control-Allow-Origin': ALLOWED_ORIGINS,
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Methods': 'POST,OPTIONS',
    }


def create_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Create a standardized API Gateway response."""
    return {
        'statusCode': status_code,
        'headers': get_cors_headers(),
        'body': json.dumps(body),
    }


def validate_feedback_request(body: Dict[str, Any]) -> Optional[str]:
    """
    Validate the feedback request body.
    
    Expected fields:
    - sessionId: The conversation session identifier
    - message: The agent's response that is receiving feedback (what the AI said)
    - feedbackType: Either 'positive' or 'negative'
    - comment (optional): User's explanation for their feedback rating
    
    Returns:
        Error message if validation fails, None if valid.
    """
    # Check required fields
    if not body.get('sessionId') or not body.get('message') or not body.get('feedbackType'):
        return 'sessionId, message, and feedbackType are required'
    
    # Validate feedbackType value
    if body['feedbackType'] not in ['positive', 'negative']:
        return 'feedbackType must be either "positive" or "negative"'
    
    # Validate sessionId format and length
    session_id = body['sessionId']
    if len(session_id) > MAX_SESSION_ID_LENGTH or not SESSION_ID_PATTERN.match(session_id):
        return f'sessionId must be alphanumeric with hyphens/underscores and max {MAX_SESSION_ID_LENGTH} characters'
    
    return None


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for feedback API.
    
    Args:
        event: API Gateway event
        context: Lambda context
        
    Returns:
        API Gateway response
    """
    try:
        # Handle OPTIONS request for CORS
        if event.get('httpMethod') == 'OPTIONS':
            return create_response(200, {})
        
        # Check for request body
        if not event.get('body'):
            return create_response(400, {'error': 'Request body is required'})
        
        # Parse request body
        try:
            body = json.loads(event['body'])
        except json.JSONDecodeError:
            return create_response(400, {'error': 'Invalid JSON format'})
        
        # Validate request
        validation_error = validate_feedback_request(body)
        if validation_error:
            return create_response(400, {'error': validation_error})
        
        # Truncate message (agent's response) if it exceeds max length
        message = body['message']
        if len(message) > MAX_MESSAGE_LENGTH:
            message = message[:MAX_MESSAGE_LENGTH]
        
        # Get optional comment (user's feedback explanation)
        comment = body.get('comment', '')
        if comment and len(comment) > MAX_MESSAGE_LENGTH:
            comment = comment[:MAX_MESSAGE_LENGTH]
        
        # Extract user ID from Cognito claims
        request_context = event.get('requestContext', {})
        authorizer = request_context.get('authorizer', {})
        claims = authorizer.get('claims', {})
        
        if not claims:
            return create_response(401, {'error': 'Unauthorized'})
        
        user_id = claims.get('sub') or 'unknown'
        
        # Generate feedback ID and timestamp
        feedback_id = str(uuid.uuid4())
        timestamp = int(time.time() * 1000)  # Milliseconds since epoch
        
        # Save to DynamoDB
        item = {
            'feedbackId': {'S': feedback_id},
            'sessionId': {'S': body['sessionId']},
            'message': {'S': message},  # Agent's response being rated
            'userId': {'S': user_id},
            'feedbackType': {'S': body['feedbackType']},
            'timestamp': {'N': str(timestamp)},
        }
        
        # Add optional comment field if provided
        if comment:
            item['comment'] = {'S': comment}  # User's explanation for their rating
        
        dynamodb.put_item(TableName=TABLE_NAME, Item=item)
        
        return create_response(200, {
            'success': True,
            'feedbackId': feedback_id,
        })
        
    except ClientError as e:
        print(f"DynamoDB error: {e.response['Error']['Message']}")
        print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}")
        return create_response(500, {'error': 'Internal server error'})
    
    except Exception as e:
        print(f"Error saving feedback: {str(e)}")
        print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}")
        return create_response(500, {'error': 'Internal server error'})
