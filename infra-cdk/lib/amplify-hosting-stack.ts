import * as cdk from "aws-cdk-lib"
import * as amplify from "@aws-cdk/aws-amplify-alpha"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"

export interface AmplifyStackProps extends cdk.NestedStackProps {
  config: AppConfig
}

export class AmplifyHostingStack extends cdk.NestedStack {
  public readonly amplifyApp: amplify.App
  public readonly amplifyUrl: string

  constructor(scope: Construct, id: string, props: AmplifyStackProps) {
    const description = "GenAIID AgentCore Starter Pack - Amplify Hosting Stack"
    super(scope, id, { ...props, description })

    // Create the Amplify app
    this.amplifyApp = new amplify.App(this, "AmplifyApp", {
      appName: `${props.config.stack_name_base}-frontend`,
      description: `${props.config.stack_name_base} - React/Next.js Frontend`,
      platform: amplify.Platform.WEB,
    })

    // Create main branch for the Amplify app
    this.amplifyApp.addBranch("main", {
      stage: "PRODUCTION",
      branchName: "main",
    })

    // The predictable domain format: https://main.{appId}.amplifyapp.com
    this.amplifyUrl = `https://main.${this.amplifyApp.appId}.amplifyapp.com`
  }
}
