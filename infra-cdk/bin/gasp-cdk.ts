#!/usr/bin/env node
import * as cdk from "aws-cdk-lib"
import { GaspMainStack } from "../lib/gasp-main-stack"
import { ConfigManager } from "../lib/utils/config-manager"

// Load configuration using ConfigManager
const configManager = new ConfigManager("config.yaml")

// Initial props consist of configuration parameters
const props = configManager.getProps()

const app = new cdk.App()

// Deploy the new Amplify-based stack that solves the circular dependency
const amplifyStack = new GaspMainStack(app, props.stack_name_base, {
  config: props,
  // If you don't specify 'env', this stack will be environment-agnostic.
  // Account/Region-dependent features and context lookups will not work,
  // but a single synthesized template can be deployed anywhere.

  // Uncomment the next line to specialize this stack for the AWS Account
  // and Region that are implied by the current CLI configuration.
  // env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION },

  // Uncomment the next line if you know exactly what Account and Region you
  // want to deploy the stack to.
  // env: { account: '123456789012', region: 'us-east-1' },

  // For more information, see https://docs.aws.amazon.com/cdk/latest/guide/environments.html
})

app.synth()
