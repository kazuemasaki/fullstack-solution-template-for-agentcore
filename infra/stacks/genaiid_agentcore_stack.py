# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import aws_cdk as cdk
from constructs import Construct

from .backend_stack import BackendStack
from .frontend_stack import GenAIIDAgentCoreFrontendStack


class GenAIIDAgentCoreStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        props: dict,
        **kwargs,
    ):
        self.props = props
        construct_id = props["stack_name_base"]
        description = "GenAIID AgentCore Starter Pack - Main Stack"
        super().__init__(scope, construct_id, description=description, **kwargs)

        # Deploy frontend stack
        self.frontend_stack = GenAIIDAgentCoreFrontendStack(
            self,
            props,
        )

        # Deploy backend stack (AgentCore runtime)
        self.backend_stack = BackendStack(
            self,
            f"{construct_id}-backend",
            config=props,
        )
