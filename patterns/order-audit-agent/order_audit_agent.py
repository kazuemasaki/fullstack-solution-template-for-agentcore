# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Order Audit Agent

An agent that audits order documents (Excel files) using:
- idp_bedrock_agent MCP server for document extraction (via MCP)
- Order Agent for waiting receipt order queries by SKU (native Strands A2A protocol)
- Gateway MCP tools for inventory check

The agent receives a presigned URL to the uploaded order document,
extracts relevant information, validates it against inventory and
product waiting receipt data via Agent-to-Agent (A2A) protocol, and provides
an audit summary.

Architecture:
- Uses native Strands A2A protocol for Order Agent communication
- IDP Agent: Document intelligence and extraction (via MCP)
- Order Agent: Waiting receipt order queries by SKU (native A2A as a Tool)
- Gateway: Direct inventory queries (via MCP)

A2A Integration:
The Order Agent is integrated using the "A2A Agent as a Tool" pattern,
which wraps the A2A agent as a standard Strands tool. This provides:
- Automatic agent card discovery
- Type-safe tool definitions
- Native A2A protocol communication
- Seamless integration with the agent toolkit
"""

import os
import traceback
from pathlib import Path
from uuid import uuid4

import boto3
import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from gateway.utils.gateway_access_token import get_gateway_access_token
from mcp.client.streamable_http import streamablehttp_client
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import Agent, tool
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


class OrderAgentA2ATool:
    """
    Order Agent A2A Tool using native Strands A2A protocol.

    This class wraps an Order Agent that supports the A2A protocol,
    allowing the audit agent to query waiting receipt orders by SKU
    through Agent-to-Agent communication.

    The tool discovers the agent card during initialization and caches
    the client for efficient repeated calls.
    """

    def __init__(self, agent_url: str, agent_name: str = "Order Agent", timeout: int = 300):
        """
        Initialize the Order Agent A2A tool.

        Args:
            agent_url: Base URL of the Order Agent A2A server
            agent_name: Display name for the agent (default: "Order Agent")
            timeout: HTTP timeout in seconds (default: 300)
        """
        self.agent_url = agent_url
        self.agent_name = agent_name
        self.timeout = timeout
        self.agent_card = None
        self.client = None
        self._initialized = False

        print(f"[ORDER AUDIT] OrderAgentA2ATool created for {agent_url}")

    async def _ensure_initialized(self):
        """
        Ensure the A2A client is initialized.

        This method is called lazily on first tool use to avoid blocking
        during agent construction.
        """
        if self._initialized:
            return

        print(f"[ORDER AUDIT] Initializing A2A client for {self.agent_name}...")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as httpx_client:
                # Discover agent card
                resolver = A2ACardResolver(httpx_client=httpx_client, base_url=self.agent_url)
                self.agent_card = await resolver.get_agent_card()
                print(f"[ORDER AUDIT] Agent card retrieved: {self.agent_card.name}")

                # Create A2A client
                config = ClientConfig(httpx_client=httpx_client, streaming=False)
                factory = ClientFactory(config)
                self.client = factory.create(self.agent_card)

                self._initialized = True
                print(f"[ORDER AUDIT] {self.agent_name} A2A client initialized successfully")

        except Exception as e:
            print(f"[ORDER AUDIT ERROR] Failed to initialize {self.agent_name}: {e}")
            raise

    @tool
    async def list_waiting_receipt_orders_by_sku(self, sku: str) -> str:
        """
        List WAITING_RECEIPT orders that include the specified SKU.

        This tool communicates with the Order Agent via A2A protocol to retrieve
        information about orders in "WAITING_RECEIPT" status that contain the
        specified product SKU. It helps assess:
        - How many units are on backorder for the product
        - Number of pending orders for the SKU
        - Delivery delay risks

        Args:
            sku: Product SKU to query (e.g., "PRD-001", "WIDGET-A"). Required.

        Returns:
            Order backlog information as a formatted string, including:
            - List of waiting receipt orders containing the SKU
            - Total units on backorder
            - Number of orders
            - Delivery status and risks

        Example:
            >>> await list_waiting_receipt_orders_by_sku(sku="PRD-001")
            "Product PRD-001 has 150 units on backorder across 3 orders in WAITING_RECEIPT status..."
        """
        await self._ensure_initialized()

        if not sku or not sku.strip():
            return "Error: SKU parameter is required"

        try:
            # Build query message for the Order Agent
            query_text = f"List WAITING_RECEIPT orders for SKU: {sku}"
            print(f"[ORDER AUDIT] Sending A2A query: {query_text}")

            # Create A2A message
            msg = Message(
                kind="message",
                role=Role.user,
                parts=[Part(TextPart(kind="text", text=query_text))],
                message_id=uuid4().hex,
            )

            # Send message and get response
            response_text = ""
            async with httpx.AsyncClient(timeout=self.timeout) as httpx_client:
                config = ClientConfig(httpx_client=httpx_client, streaming=False)
                factory = ClientFactory(config)
                client = factory.create(self.agent_card)

                async for event in client.send_message(msg):
                    if isinstance(event, Message):
                        for part in event.parts:
                            if hasattr(part, "text"):
                                response_text += part.text

            if response_text:
                print(f"[ORDER AUDIT] A2A response received: {response_text[:100]}...")
                return response_text
            else:
                return f"No response received from {self.agent_name}"

        except Exception as e:
            error_msg = f"Error contacting {self.agent_name} via A2A: {str(e)}"
            print(f"[ORDER AUDIT ERROR] {error_msg}")
            return error_msg


def create_order_agent_a2a_tool(agent_url: str) -> OrderAgentA2ATool:
    """
    Create an Order Agent A2A tool instance.

    This factory function creates and returns an OrderAgentA2ATool that can be
    added to the audit agent's toolkit.

    Args:
        agent_url: Base URL of the Order Agent A2A server

    Returns:
        OrderAgentA2ATool instance, or None if ORDER_AGENT_URL is not set
    """
    if not agent_url:
        print("[ORDER AUDIT] ORDER_AGENT_URL not set, Order Agent A2A will not be available")
        return None

    print(f"[ORDER AUDIT] Creating Order Agent A2A tool for {agent_url}")

    try:
        tool_instance = OrderAgentA2ATool(agent_url=agent_url, agent_name="Order Agent")
        print("[ORDER AUDIT] Order Agent A2A tool created successfully")
        return tool_instance

    except Exception as e:
        print(f"[ORDER AUDIT ERROR] Failed to create Order Agent A2A tool: {e}")
        print("[ORDER AUDIT] Continuing without Order Agent A2A")
        return None


def create_order_audit_agent(user_id: str, session_id: str) -> Agent:
    """
    Create the order audit agent with MCP tools, A2A tools, and memory integration.

    This agent uses:
    - Gateway MCP client for inventory queries
    - IDP MCP client for document extraction (if configured)
    - Order Agent A2A tool for waiting receipt order queries by SKU via native Strands A2A (if configured)
    - AgentCore Memory for conversation history

    Agent-to-Agent (A2A) Communication:
    - Order Agent: Uses native Strands A2A protocol with "A2A Agent as a Tool" pattern
      - Automatic agent card discovery
      - Lazy initialization on first use
      - Direct A2A protocol communication without MCP wrapper
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

        # Create MCP clients and A2A tools
        tools = []

        # Gateway client (required)
        print("[ORDER AUDIT] Step 2: Creating Gateway MCP client...")
        gateway_client = create_gateway_mcp_client(access_token)
        tools.append(gateway_client)
        print("[ORDER AUDIT] Gateway MCP client added")

        # IDP client (optional - uses IAM authentication)
        print("[ORDER AUDIT] Step 3: Creating IDP MCP client...")
        idp_client = create_idp_mcp_client()
        if idp_client:
            tools.append(idp_client)
            print("[ORDER AUDIT] IDP MCP client added")
        else:
            print("[ORDER AUDIT] IDP MCP client not available, continuing without document extraction")

        # Order Agent A2A tool (optional - uses native Strands A2A protocol)
        print("[ORDER AUDIT] Step 4: Creating Order Agent A2A tool...")
        order_agent_url = os.environ.get("ORDER_AGENT_URL")
        if order_agent_url:
            order_agent_tool = create_order_agent_a2a_tool(order_agent_url)
            if order_agent_tool:
                # Add the tool's method to the tools list
                tools.append(order_agent_tool.list_waiting_receipt_orders_by_sku)
                print("[ORDER AUDIT] Order Agent A2A tool added for native A2A communication")
            else:
                print("[ORDER AUDIT] Order Agent A2A tool creation failed")
        else:
            print("[ORDER AUDIT] ORDER_AGENT_URL not set, continuing without A2A order queries")

        print(f"[ORDER AUDIT] Step 6: Total tools loaded: {len(tools)}")
        print("[ORDER AUDIT] Step 7: Creating Agent...")
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
