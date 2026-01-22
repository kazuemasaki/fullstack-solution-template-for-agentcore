# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Order Audit Agent

An agent that audits order documents (Excel files) using:
- idp_bedrock_agent MCP server for document extraction (via MCP)
- Order Agent for waiting receipt order queries by SKU and initial order registration (native Strands A2A protocol)
- Gateway MCP tools for inventory check

The agent receives a presigned URL to the uploaded order document,
extracts relevant information, validates it against inventory and
product waiting receipt data via Agent-to-Agent (A2A) protocol, and provides
an audit summary. When audit is approved (all conditions met), it can automatically
create an initial order registration via the Order Agent.

Architecture:
- Uses native Strands A2A protocol for Order Agent communication
- IDP Agent: Document intelligence and extraction (via MCP)
- Order Agent: Waiting receipt order queries by SKU and initial order registration (native A2A as a Tool)
- Gateway: Direct inventory queries (via MCP)

A2A Integration:
The Order Agent is integrated using the "A2A Agent as a Tool" pattern,
which wraps the A2A agent as a standard Strands tool. This provides:
- Automatic agent card discovery
- Type-safe tool definitions
- Native A2A protocol communication
- Seamless integration with the agent toolkit

Order Registration Flow:
When audit result is "approved" and all conditions are satisfied:
1. Audit agent validates inventory and backorder status
2. If all checks pass, audit agent calls create_order_registration via A2A
3. Order Agent creates initial order record in Order Management API
4. Order ID is returned and included in audit report
5. Audit agent starts approval workflow via start_approval_workflow
6. Step Functions state machine sends approval email to approver
7. Order awaits approver's decision (approve/reject)
"""

import asyncio
import json
import os
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

import boto3
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
    Order Agent A2A Tool (invoked via AgentCore data plane, SigV4).

    This class wraps an Order Agent that supports the A2A protocol,
    allowing the audit agent to query waiting receipt orders by SKU
    through Agent-to-Agent communication.

    IMPORTANT:
        In our environment, the Order Agent runtime is protected by SigV4, so fetching
        `/.well-known/agent-card.json` without proper AWS authentication returns HTTP 403.
        To avoid fragile custom HTTP signing and SDK card discovery, we invoke the A2A server
        through the AgentCore data plane API (`InvokeAgentRuntime`) using boto3, which
        automatically applies SigV4 signing.
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
        self._initialized = False
        self._region = os.environ.get(
            "ORDER_AGENT_REGION",
            os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")),
        )
        self._runtime_arn: str | None = None
        self._agentcore_client = None

        print(f"[ORDER AUDIT] OrderAgentA2ATool created for {agent_url}")

    @staticmethod
    def _extract_runtime_arn_from_agent_url(agent_url: str) -> str | None:
        """Extract runtime ARN from AgentCore runtime URL (best-effort)."""
        url = (agent_url or "").strip()
        if not url:
            return None
        if url.startswith("arn:aws:bedrock-agentcore:"):
            return url
        marker = "/runtimes/"
        if marker not in url:
            return None
        rest = url.split(marker, 1)[1]
        encoded = rest.split("/invocations", 1)[0]
        if not encoded:
            return None
        return unquote(encoded)

    def _invoke_jsonrpc_sync(self, *, jsonrpc_payload: dict[str, Any]) -> str:
        """Invoke Order Agent A2A server via AgentCore data plane API (SigV4 by boto3)."""
        if self._runtime_arn is None or self._agentcore_client is None:
            raise RuntimeError("Order Agent A2A client is not initialized")
        payload_bytes = json.dumps(jsonrpc_payload, ensure_ascii=False).encode("utf-8")
        resp = self._agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=self._runtime_arn,
            contentType="application/json",
            accept="application/json",
            payload=payload_bytes,
        )
        body = resp.get("response")
        if body is None:
            raise RuntimeError("AgentCore response body is missing")
        raw = body.read()
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _extract_first_artifact_text(jsonrpc_text: str) -> str:
        """Extract artifact.parts[].text from JSON-RPC response (best-effort)."""
        candidates: list[dict[str, Any]] = []
        for line in jsonrpc_text.splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                candidates.append(obj)

        for obj in reversed(candidates):
            result = obj.get("result")
            if not isinstance(result, dict):
                continue
            artifacts = result.get("artifacts")
            if not isinstance(artifacts, list) or not artifacts:
                continue
            selected = artifacts[0] if isinstance(artifacts[0], dict) else None
            if not isinstance(selected, dict):
                continue
            parts = selected.get("parts")
            if not isinstance(parts, list) or not parts:
                continue
            part0 = parts[0]
            if isinstance(part0, dict) and isinstance(part0.get("text"), str):
                return part0["text"]
        return jsonrpc_text

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
            runtime_arn = (
                os.environ.get("ORDER_AGENT_RUNTIME_ARN")
                or self._extract_runtime_arn_from_agent_url(self.agent_url)
            )
            if not runtime_arn:
                raise RuntimeError(
                    "Order Agent runtime ARN is missing. "
                    "Set ORDER_AGENT_RUNTIME_ARN or provide ORDER_AGENT_URL in /runtimes/{arn}/invocations format."
                )
            self._runtime_arn = runtime_arn
            self._agentcore_client = boto3.client("bedrock-agentcore", region_name=self._region)

            self._initialized = True
            print(
                f"[ORDER AUDIT] {self.agent_name} A2A client initialized successfully "
                f"(runtime_arn={self._runtime_arn})"
            )

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

            jsonrpc = {
                "jsonrpc": "2.0",
                "id": f"req-{uuid4().hex}",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": query_text}],
                        "messageId": f"msg-{uuid4().hex}",
                    }
                },
            }
            raw = await asyncio.to_thread(self._invoke_jsonrpc_sync, jsonrpc_payload=jsonrpc)
            response_text = self._extract_first_artifact_text(raw)

            if response_text:
                print(f"[ORDER AUDIT] A2A response received: {response_text[:100]}...")
                return response_text
            else:
                return f"No response received from {self.agent_name}"

        except Exception as e:
            error_msg = f"Error contacting {self.agent_name} via A2A: {str(e)}"
            print(f"[ORDER AUDIT ERROR] {error_msg}")
            return error_msg

    @tool
    async def create_order_registration(
        self, supplier_id: str, items: list[dict[str, Any]], note: str = ""
    ) -> str:
        """
        Create initial order registration via Order Agent (A2A protocol).

        This tool registers a new order in the Order Management system when the
        audit result is "approved" and all conditions are met (inventory sufficient,
        no backorder risks). The order will be created with initial status and
        awaiting formal approval processing.

        IMPORTANT: Only use this tool when ALL of these conditions are met:
        - ✅ All inventory is sufficient (no shortages)
        - ✅ No delivery delay risks from backorders
        - ✅ No issues or unclear points in the order document
        - ✅ Audit result is "approval recommended"

        Args:
            supplier_id: Supplier ID from the order document (e.g., "SUP-001"). Required.
            items: List of order items in format [{"sku": "PRD-001", "qty": 100}, ...]. Required.
            note: Optional note or comment for the order (e.g., audit comments).

        Returns:
            Order creation result as a formatted string, including:
            - Created order ID (orderId)
            - Supplier ID
            - Number of items
            - Registration timestamp
            - Next steps (awaiting approval)

        Example:
            >>> await create_order_registration(
            ...     supplier_id="SUP-001",
            ...     items=[{"sku": "PRD-001", "qty": 100}, {"sku": "PRD-002", "qty": 50}],
            ...     note="Audit approved - all conditions satisfied"
            ... )
            "✅ Order registered successfully: orderId=ORD-12345..."

        Raises:
            Error if Order Agent returns an error or if parameters are invalid.
        """
        await self._ensure_initialized()

        # Validate required parameters
        if not supplier_id or not supplier_id.strip():
            return "Error: supplier_id parameter is required"

        if not items or not isinstance(items, list) or len(items) == 0:
            return "Error: items parameter is required and must be a non-empty list"

        # Validate items structure
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                return f"Error: items[{idx}] must be a dictionary with 'sku' and 'qty' keys"
            if "sku" not in item or "qty" not in item:
                return f"Error: items[{idx}] must contain both 'sku' and 'qty' keys"
            if not isinstance(item["qty"], int) or item["qty"] <= 0:
                return f"Error: items[{idx}].qty must be a positive integer"

        try:
            # Build order registration message for the Order Agent
            order_data = {"supplierId": supplier_id, "items": items}
            if note:
                order_data["note"] = note

            query_text = f"Create order registration with data: {json.dumps(order_data, ensure_ascii=False)}"
            print(f"[ORDER AUDIT] Sending A2A order registration request: supplierId={supplier_id}, items={len(items)}")

            jsonrpc = {
                "jsonrpc": "2.0",
                "id": f"req-{uuid4().hex}",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": query_text}],
                        "messageId": f"msg-{uuid4().hex}",
                    }
                },
            }
            raw = await asyncio.to_thread(self._invoke_jsonrpc_sync, jsonrpc_payload=jsonrpc)
            response_text = self._extract_first_artifact_text(raw)

            if response_text:
                print(f"[ORDER AUDIT] Order registration response: {response_text[:200]}...")
                return response_text
            else:
                return f"No response received from {self.agent_name} for order registration"

        except Exception as e:
            error_msg = f"Error registering order via {self.agent_name} A2A: {str(e)}"
            print(f"[ORDER AUDIT ERROR] {error_msg}")
            return error_msg


    @tool
    async def process_approved_order(self, order_id: str) -> str:
        """
        Process an approved order by requesting the Order Agent to execute formal order processing.

        This tool is called after an order has been approved by the approver through the
        approval workflow. It communicates with the Order Agent via A2A protocol to:
        1. Update approval status to APPROVED in Order Management API
        2. Upload the complete order document to S3

        IMPORTANT: Only use this tool when:
        - ✅ The order has been approved by an authorized approver
        - ✅ The order ID is valid and exists in the system
        - ✅ You are instructed to execute the formal order processing

        Args:
            order_id: Order ID that has been approved (e.g., "9cea99bb-ae30-47cf-92df-52f49e260680"). Required.

        Returns:
            Order processing result as a formatted string, including:
            - Order ID
            - Supplier ID
            - S3 bucket and key where order document is stored
            - Processing status
            - Timestamp

        Example:
            >>> await process_approved_order(order_id="9cea99bb-ae30-47cf-92df-52f49e260680")
            "✅ Order processed successfully: orderId=9cea99bb-ae30-47cf-92df-52f49e260680, s3://bucket/SUP-001/order.json"

        Raises:
            Error if Order Agent returns an error or if the order processing fails.
        """
        await self._ensure_initialized()

        if not order_id or not order_id.strip():
            return "Error: order_id parameter is required"

        try:
            # Build order processing message for the Order Agent
            # Request to use finalize_approved_order tool
            query_text = f"注文番号 {order_id} が承認されました。finalize_approved_order ツールを使用して正式な発注処理を実行してください。"
            print(f"[ORDER AUDIT] Sending A2A order processing request: orderId={order_id}")

            jsonrpc = {
                "jsonrpc": "2.0",
                "id": f"req-{uuid4().hex}",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": query_text}],
                        "messageId": f"msg-{uuid4().hex}",
                    }
                },
            }
            raw = await asyncio.to_thread(self._invoke_jsonrpc_sync, jsonrpc_payload=jsonrpc)
            response_text = self._extract_first_artifact_text(raw)

            if response_text:
                print(f"[ORDER AUDIT] Order processing response: {response_text[:200]}...")
                return response_text
            else:
                return f"No response received from {self.agent_name} for order processing"

        except Exception as e:
            error_msg = f"Error processing approved order via {self.agent_name} A2A: {str(e)}"
            print(f"[ORDER AUDIT ERROR] {error_msg}")
            return error_msg

    @tool
    async def start_approval_workflow(self, order_id: str) -> str:
        """
        Start the order approval workflow in Step Functions.

        This tool initiates the approval state machine for a successfully registered order.
        The workflow will send an approval email to the designated approver and wait for
        their response.

        IMPORTANT: Only use this tool AFTER successful order registration via create_order_registration.
        The order_id parameter should be the orderId returned from create_order_registration.

        Args:
            order_id: Order ID returned from create_order_registration (e.g., "9cea99bb-ae30-47cf-92df-52f49e260680"). Required.

        Returns:
            Workflow execution result as a formatted string, including:
            - Execution ARN
            - State machine ARN
            - Approval status (pending)
            - Next steps for approver

        Example:
            >>> await start_approval_workflow(order_id="9cea99bb-ae30-47cf-92df-52f49e260680")
            "✅ Approval workflow started: executionArn=arn:aws:states:..."

        Raises:
            Error if Step Functions execution fails or if environment variables are not set.
        """
        if not order_id or not order_id.strip():
            return "Error: order_id parameter is required"

        # Get Step Functions configuration from environment
        state_machine_arn = os.environ.get("APPROVAL_STATE_MACHINE_ARN")
        approver_email = os.environ.get("APPROVAL_APPROVER_EMAIL")

        if not state_machine_arn:
            return "Error: APPROVAL_STATE_MACHINE_ARN environment variable is not set"

        if not approver_email:
            return "Error: APPROVAL_APPROVER_EMAIL environment variable is not set"

        try:
            # Prepare Step Functions input
            execution_input = {"orderId": order_id, "approverEmail": approver_email}

            print(f"[ORDER AUDIT] Starting approval workflow for order: {order_id}")
            print(f"[ORDER AUDIT] State machine: {state_machine_arn}")
            print(f"[ORDER AUDIT] Approver: {approver_email}")

            # Create Step Functions client
            sfn_client = boto3.client("stepfunctions", region_name=os.environ.get("AWS_REGION", "us-east-1"))

            # Start execution
            response = sfn_client.start_execution(
                stateMachineArn=state_machine_arn,
                name=f"approval-{order_id}-{uuid4().hex[:8]}",  # Unique execution name
                input=json.dumps(execution_input),
            )

            execution_arn = response["executionArn"]
            start_date = response["startDate"].isoformat()

            print(f"[ORDER AUDIT] Approval workflow started: {execution_arn}")

            result_message = (
                f"✅ 承認ワークフローを開始しました\n\n"
                f"**発注番号**: {order_id}\n"
                f"**承認者**: {approver_email}\n"
                f"**実行ARN**: {execution_arn}\n"
                f"**開始時刻**: {start_date}\n\n"
                f"承認者に承認リンクを含むメールが送信されます。\n"
                f"承認者がリンクをクリックして承認または却下を選択するまで、ワークフローは待機状態になります。"
            )

            return result_message

        except Exception as e:
            error_msg = f"Error starting approval workflow: {str(e)}"
            print(f"[ORDER AUDIT ERROR] {error_msg}")
            return f"❌ 承認ワークフローの開始に失敗しました: {str(e)}"


def create_order_agent_a2a_tool(agent_url: str) -> OrderAgentA2ATool:
    """
    Create an Order Agent A2A tool instance.

    This factory function creates and returns an OrderAgentA2ATool that can be
    added to the audit agent's toolkit.

    Note:
        AgentCore Runtime URLs often appear in multiple shapes depending on context.
        For Strands A2A, the base_url MUST point to the A2A server root where
        `/.well-known/agent-card.json` is reachable. In our environment, the Order
        Agent serves A2A under the `/invocations` path, so we normalize:

        - Remove any query string (e.g. `?qualifier=DEFAULT`) because it breaks path joins
        - Ensure the URL ends with `/invocations`
    """

    def _normalize_agentcore_a2a_base_url(url: str) -> str:
        """Normalize AgentCore runtime URL for Strands A2A card discovery."""
        normalized = (url or "").strip()
        # Drop query string (e.g., ?qualifier=DEFAULT) so that base_url path joins are valid.
        normalized = normalized.split("?", 1)[0]
        normalized = normalized.rstrip("/")

        # Ensure `/invocations` suffix (A2A is mounted under this path for our runtime).
        if "/invocations" in normalized:
            prefix = normalized.split("/invocations", 1)[0]
            normalized = f"{prefix}/invocations"
        else:
            normalized = f"{normalized}/invocations"

        return normalized

    if not agent_url:
        print("[ORDER AUDIT] ORDER_AGENT_URL not set, Order Agent A2A will not be available")
        return None

    normalized_url = _normalize_agentcore_a2a_base_url(agent_url)
    print(f"[ORDER AUDIT] Creating Order Agent A2A tool for {normalized_url}")

    try:
        tool_instance = OrderAgentA2ATool(agent_url=normalized_url, agent_name="Order Agent")
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
    - Order Agent A2A tools via native Strands A2A (if configured):
      - list_waiting_receipt_orders_by_sku: Query backlog by SKU
      - create_order_registration: Create initial order registration when audit is approved
      - start_approval_workflow: Start Step Functions approval workflow after order registration
      - process_approved_order: Execute formal order processing after approval
    - AgentCore Memory for conversation history

    Agent-to-Agent (A2A) Communication:
    - Order Agent: Uses native Strands A2A protocol with "A2A Agent as a Tool" pattern
      - Automatic agent card discovery
      - Lazy initialization on first use
      - Direct A2A protocol communication without MCP wrapper

    Approval Workflow Integration:
    - After successful order registration, agent starts Step Functions state machine
    - Approval email is sent to designated approver
    - Workflow awaits approver's decision (approve/reject)
    - When approved, external Lambda invokes this agent to process the order
    - Agent then requests Order Agent to execute formal order processing via A2A
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
                # Add the tool's methods to the tools list
                tools.append(order_agent_tool.list_waiting_receipt_orders_by_sku)
                tools.append(order_agent_tool.create_order_registration)
                tools.append(order_agent_tool.start_approval_workflow)
                tools.append(order_agent_tool.process_approved_order)
                print("[ORDER AUDIT] Order Agent A2A tools added for native A2A communication")
                print("[ORDER AUDIT] - list_waiting_receipt_orders_by_sku (backlog query)")
                print("[ORDER AUDIT] - create_order_registration (initial order registration)")
                print("[ORDER AUDIT] - start_approval_workflow (approval workflow initiation)")
                print("[ORDER AUDIT] - process_approved_order (approved order processing)")
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
