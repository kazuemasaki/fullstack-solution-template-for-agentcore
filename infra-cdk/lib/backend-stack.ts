import * as cdk from "aws-cdk-lib"
import * as cognito from "aws-cdk-lib/aws-cognito"
import * as ecr from "aws-cdk-lib/aws-ecr"
import * as codebuild from "aws-cdk-lib/aws-codebuild"
import * as iam from "aws-cdk-lib/aws-iam"
import * as s3Assets from "aws-cdk-lib/aws-s3-assets"
import * as ssm from "aws-cdk-lib/aws-ssm"
// Note: Using CfnResource for BedrockAgentCore as the L2 construct may not be available yet
import { PythonFunction } from "@aws-cdk/aws-lambda-python-alpha"
import * as lambda from "aws-cdk-lib/aws-lambda"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"
import { AgentCoreRole } from "./utils/agentcore-role"
import * as path from "path"

export interface BackendStackProps extends cdk.NestedStackProps {
  config: AppConfig
  userPoolId: string
  userPoolClientId: string
}

export class BackendStack extends cdk.NestedStack {
  public readonly userPoolId: string
  public readonly userPoolClientId: string
  public runtimeArn: string
  public ecrRepository: ecr.Repository
  public buildProject: codebuild.Project
  private agentName: cdk.CfnParameter
  private imageTag: cdk.CfnParameter
  private networkMode: cdk.CfnParameter

  constructor(scope: Construct, id: string, props: BackendStackProps) {
    super(scope, id, props)

    // Store the Cognito values
    this.userPoolId = props.userPoolId
    this.userPoolClientId = props.userPoolClientId

    // Create ECR repository and CodeBuild project
    this.createECRAndCodeBuild(props.config)

    // Create AgentCore Runtime resources
    this.createAgentCoreRuntime(props.config)

    // Store runtime ARN in SSM for frontend stack
    this.createRuntimeSSMParameters(props.config)
  }

  private createECRAndCodeBuild(config: AppConfig): void {
    const pattern = config.backend?.pattern || "strands-single-agent"

    // Parameters
    this.agentName = new cdk.CfnParameter(this, "AgentName", {
      type: "String",
      default: "StrandsAgent",
      description: "Name for the agent runtime",
    })

    this.imageTag = new cdk.CfnParameter(this, "ImageTag", {
      type: "String",
      default: "latest",
      description: "Tag for the Docker image",
    })

    this.networkMode = new cdk.CfnParameter(this, "NetworkMode", {
      type: "String",
      default: "PUBLIC",
      description: "Network mode for AgentCore resources",
      allowedValues: ["PUBLIC", "PRIVATE"],
    })

    // ECR Repository
    this.ecrRepository = new ecr.Repository(this, "ECRRepository", {
      repositoryName: `${config.stack_name_base.toLowerCase()}-${pattern}`,
      imageTagMutability: ecr.TagMutability.MUTABLE,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: true,
      imageScanOnPush: true,
    })

    // S3 Asset for source code
    const patternPath = path.join(__dirname, "..", "..", "patterns", pattern)
    const sourceAsset = new s3Assets.Asset(this, "SourceAsset", {
      path: patternPath,
    })

    // CodeBuild Role
    const codebuildRole = new iam.Role(this, "CodeBuildRole", {
      assumedBy: new iam.ServicePrincipal("codebuild.amazonaws.com"),
      inlinePolicies: {
        CodeBuildPolicy: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              sid: "CloudWatchLogs",
              effect: iam.Effect.ALLOW,
              actions: ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
              resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/aws/codebuild/*`],
            }),
            new iam.PolicyStatement({
              sid: "ECRAccess",
              effect: iam.Effect.ALLOW,
              actions: [
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "ecr:GetAuthorizationToken",
                "ecr:PutImage",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
              ],
              resources: [this.ecrRepository.repositoryArn, "*"],
            }),
            new iam.PolicyStatement({
              sid: "S3SourceAccess",
              effect: iam.Effect.ALLOW,
              actions: ["s3:GetObject"],
              resources: [`${sourceAsset.bucket.bucketArn}/*`],
            }),
          ],
        }),
      },
    })

    // CodeBuild Project
    this.buildProject = new codebuild.Project(this, "AgentImageBuildProject", {
      projectName: `${config.stack_name_base}-${pattern}-build`,
      description: `Build ${pattern} agent Docker image for ${config.stack_name_base}`,
      role: codebuildRole,
      environment: {
        buildImage: codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
        computeType: codebuild.ComputeType.LARGE,
        privileged: true,
      },
      source: codebuild.Source.s3({
        bucket: sourceAsset.bucket,
        path: sourceAsset.s3ObjectKey,
      }),
      buildSpec: codebuild.BuildSpec.fromObject({
        version: "0.2",
        phases: {
          pre_build: {
            commands: [
              "echo Logging in to Amazon ECR...",
              "aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com",
            ],
          },
          build: {
            commands: [
              "echo Build started on `date`",
              "echo Building the Docker image for agent ARM64...",
              "docker build -t $IMAGE_REPO_NAME:$IMAGE_TAG .",
              "docker tag $IMAGE_REPO_NAME:$IMAGE_TAG $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG",
            ],
          },
          post_build: {
            commands: [
              "echo Build completed on `date`",
              "echo Pushing the Docker image...",
              "docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG",
              "echo ARM64 Docker image pushed successfully",
            ],
          },
        },
      }),
      environmentVariables: {
        AWS_DEFAULT_REGION: {
          value: this.region,
        },
        AWS_ACCOUNT_ID: {
          value: this.account,
        },
        IMAGE_REPO_NAME: {
          value: this.ecrRepository.repositoryName,
        },
        IMAGE_TAG: {
          value: this.imageTag.valueAsString,
        },
        STACK_NAME: {
          value: config.stack_name_base,
        },
      },
    })
  }

  private createAgentCoreRuntime(config: AppConfig): void {
    const pattern = config.backend?.pattern || "strands-single-agent"

    // Lambda function to trigger and wait for CodeBuild using Python 3.13
    const buildTriggerFunction = new PythonFunction(this, "BuildTriggerFunction", {
      entry: path.join(__dirname, "utils", "build-trigger-lambda"),
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: "handler",
      timeout: cdk.Duration.minutes(15),
      initialPolicy: [
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ["codebuild:StartBuild", "codebuild:BatchGetBuilds"],
          resources: [this.buildProject.projectArn],
        }),
      ],
    })

    // Custom Resource using the Lambda function
    const triggerBuild = new cdk.CustomResource(this, "TriggerImageBuild", {
      serviceToken: buildTriggerFunction.functionArn,
      properties: {
        ProjectName: this.buildProject.projectName,
      },
    })

    // Create AgentCore execution role
    const agentRole = new AgentCoreRole(this, "AgentCoreRole")

    // Create AgentCore Runtime with JWT authorizer using CloudFormation resource
    const agentRuntime = new cdk.CfnResource(this, "AgentRuntime", {
      type: "AWS::BedrockAgentCore::Runtime",
      properties: {
        AgentRuntimeName: `${config.stack_name_base.replace(/-/g, "_")}_${
          this.agentName.valueAsString
        }`,
        AgentRuntimeArtifact: {
          ContainerConfiguration: {
            ContainerUri: `${this.ecrRepository.repositoryUri}:${this.imageTag.valueAsString}`,
          },
        },
        NetworkConfiguration: {
          NetworkMode: this.networkMode.valueAsString,
        },
        ProtocolConfiguration: "HTTP",
        RoleArn: agentRole.roleArn,
        Description: `${pattern} agent runtime for ${config.stack_name_base}`,
        EnvironmentVariables: {
          AWS_DEFAULT_REGION: this.region,
        },
        // Add JWT authorizer with Cognito configuration
        AuthorizerConfiguration: {
          CustomJWTAuthorizer: {
            DiscoveryUrl: `https://cognito-idp.${this.region}.amazonaws.com/${this.userPoolId}/.well-known/openid-configuration`,
            AllowedClients: [this.userPoolClientId],
          },
        },
      },
    })

    agentRuntime.node.addDependency(triggerBuild)

    // Store the runtime ARN
    this.runtimeArn = agentRuntime.getAtt("AgentRuntimeArn").toString()

    // Ensure the custom resource depends on the build project
    triggerBuild.node.addDependency(this.buildProject)
  }

  private createRuntimeSSMParameters(config: AppConfig): void {
    // Store runtime ARN in SSM for frontend stack
    new ssm.StringParameter(this, "RuntimeArnParam", {
      parameterName: `/${config.stack_name_base}/runtime-arn`,
      stringValue: this.runtimeArn,
    })
  }
}
