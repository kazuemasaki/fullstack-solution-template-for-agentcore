# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Order Audit Agent

An agent that audits order documents (Excel files) using:
- idp_bedrock_agent MCP server for document extraction
- Gateway MCP tools for inventory check and backlog query

The agent receives a presigned URL to the uploaded order document,
extracts relevant information, validates it against inventory and
customer backlog data, and provides an audit summary.
"""

import os
import traceback
from pathlib import Path

import boto3
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from gateway.utils.gateway_access_token import get_gateway_access_token
from mcp.client.streamable_http import streamablehttp_client
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

app = BedrockAgentCoreApp()


def load_system_prompt() -> str:
    """
    Load system prompt from external Markdown file.

    This allows non-technical users to easily modify the agent's behavior
    by editing the system_prompt.md file without touching Python code.

    Returns:
        str: The system prompt text loaded from the Markdown file.

    Raises:
        FileNotFoundError: If system_prompt.md is not found in the expected location.
        IOError: If there's an error reading the file.
    """
    # Get the directory where this script is located
    script_dir = Path(__file__).parent

    # Path to the system prompt Markdown file
    prompt_file = script_dir / "system_prompt.md"

    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            content = f.read()
            print(f"[ORDER AUDIT] System prompt loaded from {prompt_file}")
            return content
    except FileNotFoundError:
        error_msg = f"System prompt file not found: {prompt_file}"
        print(f"[ORDER AUDIT ERROR] {error_msg}")
        raise FileNotFoundError(error_msg)
    except Exception as e:
        error_msg = f"Failed to load system prompt from {prompt_file}: {e}"
        print(f"[ORDER AUDIT ERROR] {error_msg}")
        raise IOError(error_msg)


# Load system prompt from external file
SYSTEM_PROMPT = load_system_prompt()


def get_ssm_parameter(parameter_name: str, with_decryption: bool = False) -> str:
    """
    Fetch parameter from SSM Parameter Store.

    SSM Parameter Store is AWS's service for storing configuration values securely.
    This function retrieves values like Gateway URLs that are set during deployment.
    """
    region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    ssm = boto3.client("ssm", region_name=region)
    try:
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=with_decryption)
        return response["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        raise ValueError(f"SSM parameter not found: {parameter_name}")
    except Exception as e:
        raise ValueError(f"Failed to retrieve SSM parameter {parameter_name}: {e}")


def get_secret(secret_name: str) -> str:
    """Fetch secret from Secrets Manager."""
    region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    client = boto3.client("secretsmanager", region_name=region)
    try:
        response = client.get_secret_value(SecretId=secret_name)
        return response["SecretString"]
    except Exception as e:
        raise ValueError(f"Failed to retrieve secret {secret_name}: {e}")


def create_gateway_mcp_client(access_token: str) -> MCPClient:
    """
    Create MCP client for AgentCore Gateway with OAuth2 authentication.

    This client connects to the Gateway which provides access to:
    - check_inventory: Stock level checking
    - query_order_backlog: Customer backlog queries
    """
    stack_name = os.environ.get("STACK_NAME")
    if not stack_name:
        raise ValueError("STACK_NAME environment variable is required")

    # Validate stack name format to prevent injection
    if not stack_name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid STACK_NAME format")

    print(f"[ORDER AUDIT] Creating Gateway MCP client for stack: {stack_name}")

    # Fetch Gateway URL from SSM
    gateway_url = get_ssm_parameter(f"/{stack_name}/gateway_url")
    print(f"[ORDER AUDIT] Gateway URL from SSM: {gateway_url}")

    # Create MCP client with Bearer token authentication
    gateway_client = MCPClient(
        lambda: streamablehttp_client(url=gateway_url, headers={"Authorization": f"Bearer {access_token}"}),
        prefix="gateway",
    )

    print("[ORDER AUDIT] Gateway MCP client created successfully")
    return gateway_client


def create_idp_mcp_client() -> MCPClient:
    """
    Create MCP client for idp_bedrock_agent using IAM SigV4 authentication.

    This client connects to the IDP agent which provides:
    - extract_document_attributes: Extract structured data from documents
    - get_extraction_status: Check extraction job status
    - get_bucket_info: Get S3 bucket information

    Note: Uses IAM SigV4 authentication instead of Cognito OAuth2.
    """
    idp_agent_url = os.environ.get("IDP_AGENT_URL")
    if not idp_agent_url:
        print("[ORDER AUDIT] IDP_AGENT_URL not set, IDP tools will not be available")
        return None

    idp_region = os.environ.get("IDP_AGENT_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    print(f"[ORDER AUDIT] Creating IDP MCP client with IAM SigV4 (region: {idp_region})")

    try:
        idp_client = MCPClient(
            lambda: aws_iam_streamablehttp_client(
                endpoint=idp_agent_url,
                aws_region=idp_region,
                aws_service="bedrock-agentcore"
            ),
            prefix="idp",
        )
        print("[ORDER AUDIT] IDP MCP client created successfully")
        return idp_client

    except Exception as e:
        print(f"[ORDER AUDIT] Failed to create IDP MCP client: {e}")
        print("[ORDER AUDIT] Continuing without IDP tools")
        return None


def create_order_audit_agent(user_id: str, session_id: str) -> Agent:
    """
    Create the order audit agent with MCP tools and memory integration.

    This agent uses:
    - Gateway MCP client for inventory and backlog queries
    - IDP MCP client for document extraction (if configured)
    - AgentCore Memory for conversation history
    """
    bedrock_model = BedrockModel(model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0", temperature=0.1)

    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")

    # Configure AgentCore Memory
    agentcore_memory_config = AgentCoreMemoryConfig(memory_id=memory_id, session_id=session_id, actor_id=user_id)

    session_manager = AgentCoreMemorySessionManager(
        agentcore_memory_config=agentcore_memory_config,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )

    try:
        print("[ORDER AUDIT] Starting agent creation with MCP tools...")

        # Get OAuth2 access token
        print("[ORDER AUDIT] Step 1: Getting OAuth2 access token...")
        access_token = get_gateway_access_token()
        print(f"[ORDER AUDIT] Got access token: {access_token[:20]}...")

        # Create MCP clients
        tools = []

        # Gateway client (required)
        print("[ORDER AUDIT] Step 2: Creating Gateway MCP client...")
        gateway_client = create_gateway_mcp_client(access_token)
        tools.append(gateway_client)
        print("[ORDER AUDIT] Gateway MCP client added")

        # IDP client (optional - uses separate authentication)
        print("[ORDER AUDIT] Step 3: Creating IDP MCP client...")
        idp_client = create_idp_mcp_client()
        if idp_client:
            tools.append(idp_client)
            print("[ORDER AUDIT] IDP MCP client added")
        else:
            print("[ORDER AUDIT] IDP MCP client not available, continuing without document extraction")

        print("[ORDER AUDIT] Step 4: Creating Agent...")
        agent = Agent(
            name="OrderAuditAgent",
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
            model=bedrock_model,
            session_manager=session_manager,
            trace_attributes={
                "user.id": user_id,
                "session.id": session_id,
                "agent.type": "order-audit",
            },
        )
        print("[ORDER AUDIT] Agent created successfully")
        return agent

    except Exception as e:
        print(f"[ORDER AUDIT ERROR] Error creating agent: {e}")
        print(f"[ORDER AUDIT ERROR] Exception type: {type(e).__name__}")
        print("[ORDER AUDIT ERROR] Traceback:")
        traceback.print_exc()
        raise


@app.entrypoint
async def agent_stream(payload):
    """
    Main entrypoint for the order audit agent.

    This function:
    1. Receives the user's query (typically containing a presigned URL)
    2. Creates an agent with MCP tools
    3. Processes the request with streaming response
    """
    user_query = payload.get("prompt")
    user_id = payload.get("userId")
    session_id = payload.get("runtimeSessionId")

    if not all([user_query, user_id, session_id]):
        yield {
            "status": "error",
            "error": "Missing required fields: prompt, userId, or runtimeSessionId",
        }
        return

    try:
        print(f"[ORDER AUDIT STREAM] Starting for user: {user_id}, session: {session_id}")
        print(f"[ORDER AUDIT STREAM] Query: {user_query[:200]}...")  # Log first 200 chars

        agent = create_order_audit_agent(user_id, session_id)

        # Use the agent's stream_async method for token-level streaming
        async for event in agent.stream_async(user_query):
            yield event

    except Exception as e:
        print(f"[ORDER AUDIT ERROR] Error in agent_stream: {e}")
        traceback.print_exc()
        yield {"status": "error", "error": str(e)}


if __name__ == "__main__":
    app.run()
