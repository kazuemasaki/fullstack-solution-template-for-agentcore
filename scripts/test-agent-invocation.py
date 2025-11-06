#!/usr/bin/env python3

"""
Interactive agent chat tester for local and remote agents

Tests agent invocation with conversation continuity:
- Remote mode (default): Chat with deployed agent via Cognito authentication
- Local mode (--local): Chat with agent running on localhost:8080

Usage:
    # Remote agent testing (prompts for credentials)
    uv run scripts/test-agent-invocation.py

    # Local agent testing (agent must be running on localhost:8080)
    uv run scripts/test-agent-invocation.py --local
"""

import sys
import time
import socket
import argparse
import getpass
import subprocess
import signal
import atexit
from pathlib import Path
from typing import Dict, Optional
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
    generate_session_id,
    print_msg,
    print_section,
)

# Global variable to track agent process
_agent_process: Optional[subprocess.Popen] = None


def generate_trace_id() -> str:
    """Generate X-Amzn-Trace-Id header value for AWS request tracing."""
    timestamp_hex = format(int(time.time()), 'x')
    return f"1-{timestamp_hex}-{generate_session_id()}"


def check_port_available(port: int = 8080) -> bool:
    """Check if a port is available for connection."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        result = sock.connect_ex(('localhost', port))
        sock.close()
        return result == 0
    except Exception:
        return False


def start_local_agent(memory_id: str, region: str) -> subprocess.Popen:
    """
    Start the local agent in a background process.
    
    Args:
        memory_id: Memory ID for the agent
        region: AWS region
    
    Returns:
        Subprocess object for the running agent
    """
    global _agent_process
    
    agent_path = Path(__file__).parent.parent / "patterns" / "strands-single-agent" / "basic_agent.py"
    
    if not agent_path.exists():
        print_msg(f"Agent file not found: {agent_path}", "error")
        sys.exit(1)
    
    print(f"Starting local agent at {agent_path}...")
    print(f"  Memory ID: {memory_id}")
    print(f"  Region: {region}\n")
    
    # Set up environment variables
    env = {
        **dict(subprocess.os.environ),
        "MEMORY_ID": memory_id,
        "AWS_DEFAULT_REGION": region,
    }
    
    # Start agent process
    try:
        _agent_process = subprocess.Popen(
            ["uv", "run", str(agent_path)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait for agent to start (check port becomes available)
        print("Waiting for agent to start on port 8080...")
        for i in range(30):  # Wait up to 30 seconds
            if check_port_available(8080):
                print_msg("Agent started successfully", "success")
                return _agent_process
            time.sleep(1)
        
        print_msg("Agent failed to start (timeout)", "error")
        _agent_process.terminate()
        sys.exit(1)
        
    except Exception as e:
        print_msg(f"Failed to start agent: {e}", "error")
        sys.exit(1)


def stop_local_agent() -> None:
    """Stop the local agent process if running."""
    global _agent_process
    if _agent_process:
        print("\nStopping local agent...")
        _agent_process.terminate()
        try:
            _agent_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _agent_process.kill()
        print_msg("Agent stopped", "success")


# Register cleanup handler
atexit.register(stop_local_agent)


def signal_handler(sig, frame):
    """Handle interrupt signal."""
    print("\n")
    stop_local_agent()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


def invoke_agent_local(prompt: str, session_id: str) -> str:
    """
    Invoke agent running locally on localhost:8080.
    
    Note: Agent must have MEMORY_ID and AWS_DEFAULT_REGION environment variables set.
    
    Args:
        prompt: User prompt/query
        session_id: Session ID for conversation continuity
    
    Returns:
        Agent's raw response
    """
    url = "http://localhost:8080/invocations"
    
    payload = {
        "prompt": prompt,
        "runtimeSessionId": session_id,
        "userId": "local-test-user",
    }
    
    try:
        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            # Return raw response text to show complete agent output
            return response.text
        else:
            return f"Error: HTTP {response.status_code}: {response.text}"
            
    except requests.exceptions.ConnectionError:
        print_msg("Could not connect to localhost:8080", "error")
        print("\nMake sure your agent is running with required environment variables:")
        print("  MEMORY_ID=<memory-id> AWS_DEFAULT_REGION=<region> uv run basic_agent.py")
        print("\nGet memory ID from: uv run scripts/test-memory.py")
        sys.exit(1)
    except Exception as e:
        return f"Error: {e}"


def invoke_agent_remote(
    prompt: str,
    session_id: str,
    user_id: str,
    access_token: str,
    runtime_arn: str,
    region: str
) -> str:
    """
    Invoke deployed agent via AgentCore endpoint with streaming support.
    
    Args:
        prompt: User prompt/query
        session_id: Session ID for conversation continuity
        user_id: User ID from Cognito authentication
        access_token: Cognito access token
        runtime_arn: Agent runtime ARN
        region: AWS region
    
    Returns:
        Agent's complete response text
    """
    endpoint = f"https://bedrock-agentcore.{region}.amazonaws.com"
    escaped_arn = requests.utils.quote(runtime_arn, safe='')
    url = f"{endpoint}/runtimes/{escaped_arn}/invocations?qualifier=DEFAULT"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Amzn-Trace-Id": generate_trace_id(),
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }
    
    payload = {
        "prompt": prompt,
        "runtimeSessionId": session_id,
        "userId": user_id,
    }
    
    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            stream=True,
            timeout=60
        )
        
        if response.status_code != 200:
            return f"Error: HTTP {response.status_code}: {response.text}"
        
        # Handle streaming response
        completion = ""
        for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
            if chunk:
                completion += chunk
        
        return completion
        
    except Exception as e:
        return f"Error: {e}"


def run_chat(local_mode: bool, config: Dict[str, str]) -> None:
    """
    Run interactive chat session.
    
    Args:
        local_mode: Whether to use local mode
        config: Configuration dictionary
    """
    session_id = generate_session_id()
    
    print_section("Interactive Agent Chat")
    print(f"Session ID: {session_id}")
    print(f"Mode: {'Local (localhost:8080)' if local_mode else 'Remote (deployed agent)'}")
    print(f"\n{Fore.YELLOW}💡 Type 'exit' or 'quit' to end, or press Ctrl+C{Style.RESET_ALL}\n")
    
    while True:
        try:
            prompt = input(f"{Fore.CYAN}You:{Style.RESET_ALL} ").strip()
            
            if not prompt:
                continue
            
            if prompt.lower() in ['exit', 'quit']:
                print(f"\n{Fore.GREEN}Goodbye!{Style.RESET_ALL}")
                break
            
            # Invoke agent
            print(f"{Fore.YELLOW}Agent:{Style.RESET_ALL} ", end='', flush=True)
            start_time = time.time()
            
            if local_mode:
                response = invoke_agent_local(prompt, session_id)
            else:
                response = invoke_agent_remote(
                    prompt=prompt,
                    session_id=session_id,
                    user_id=config["user_id"],
                    access_token=config["access_token"],
                    runtime_arn=config["runtime_arn"],
                    region=config["region"]
                )
            
            elapsed = time.time() - start_time
            print(response)
            print()
            
        except KeyboardInterrupt:
            print(f"\n\n{Fore.GREEN}Goodbye!{Style.RESET_ALL}")
            break
        except EOFError:
            print(f"\n\n{Fore.GREEN}Goodbye!{Style.RESET_ALL}")
            break


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Interactive agent chat tester (local or remote)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Remote agent (prompts for credentials)
  uv run scripts/test-agent-invocation.py
  
  # Local agent on localhost:8080
  uv run scripts/test-agent-invocation.py --local

Notes:
  - For memory testing, use: uv run scripts/test-memory.py
  - Always runs in interactive conversation mode
        """
    )
    
    parser.add_argument(
        "--local",
        action="store_true",
        help="Test local agent on localhost:8080 (default: remote)"
    )
    
    return parser.parse_args()


def main():
    """Main entry point."""
    print("=" * 60)
    print("AgentCore Interactive Chat Tester")
    print("=" * 60 + "\n")
    
    args = parse_arguments()
    config: Dict[str, str] = {}
    
    # LOCAL MODE
    if args.local:
        print_section("LOCAL MODE - Auto-starting local agent")
        
        # Get memory configuration
        stack_cfg = get_stack_config()
        memory_arn = stack_cfg['outputs']['MemoryArn']
        memory_id = memory_arn.split("/")[-1]
        region = stack_cfg['region']
        
        # Check if agent is already running
        if check_port_available(8080):
            print_msg("Agent already running on localhost:8080", "info")
            print("Using existing agent instance...\n")
        else:
            # Start the agent
            start_local_agent(memory_id, region)
    
    # REMOTE MODE
    else:
        print_section("REMOTE MODE - Testing deployed agent")
        
        stack_cfg = get_stack_config()
        print(f"Stack: {stack_cfg['stack_name']}\n")
        
        # Fetch SSM params
        print("Fetching configuration...")
        params = get_ssm_params(
            stack_cfg['stack_name'],
            'cognito-user-pool-id',
            'cognito-user-pool-client-id',
            'runtime-arn'
        )
        print_msg("Configuration fetched")
        
        runtime_arn = params['runtime-arn']
        region = runtime_arn.split(":")[3]
        
        # Get credentials
        print_section("Authentication")
        
        username = input("Enter username: ").strip()
        if not username:
            print_msg("Username is required", "error")
            sys.exit(1)
        password = getpass.getpass(f"Enter password for {username}: ")
        
        # Authenticate
        access_token, user_id = authenticate_cognito(
            params['cognito-user-pool-id'],
            params['cognito-user-pool-client-id'],
            username,
            password
        )
        
        config["access_token"] = access_token
        config["user_id"] = user_id
        config["runtime_arn"] = runtime_arn
        config["region"] = region
        
        print(f"\nRuntime ARN: {runtime_arn}")
        print(f"Region: {region}\n")
    
    # Run interactive chat
    run_chat(args.local, config)


if __name__ == "__main__":
    main()
