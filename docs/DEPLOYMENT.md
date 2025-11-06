# Deployment Guide

This guide walks you through deploying the GenAIID AgentCore Starter Pack (GASP) to AWS.

## Prerequisites

Before deploying, ensure you have:

- **Node.js 18+** installed (see [AWS guide for installing Node.js on EC2](https://docs.aws.amazon.com/sdk-for-javascript/v2/developer-guide/setting-up-node-on-ec2-instance.html))
- **AWS CLI** configured with appropriate permissions
- **AWS CDK CLI** installed: `npm install -g aws-cdk` (see [CDK Getting Started guide](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html))
- **Docker** installed. (see [Install Docker Engine](https://docs.docker.com/engine/install/))
- An AWS account with sufficient permissions to create:
  - S3 buckets
  - CloudFront distributions
  - Cognito User Pools
  - ECR repositories
  - CodeBuild projects
  - Bedrock AgentCore resources
  - IAM roles and policies

## Configuration

### 1. Update Configuration File

Edit `infra-cdk/config.yaml` to customize your deployment:

```yaml
stack_name_base: "your-project-name"  # Change this to your preferred stack name

admin_user_email: null  # Optional: admin@example.com (auto-creates user & emails credentials)

frontend:
  domain_name: null  # Optional: Set to your custom domain (e.g., "app.example.com")
  certificate_arn: null  # Optional: Set to your ACM certificate ARN if using custom domain

backend:
  pattern: "strands-single-agent"  # Available patterns: strands-single-agent
```

**Important**: Change `stack_name_base` to a unique name for your project to avoid conflicts.

## Deployment Steps

### 1. Install Dependencies

Install infrastructure dependencies:
```bash
cd infra-cdk
npm install
cd ..
```

**Note**: Frontend dependencies are automatically installed during deployment via Docker bundling, so no separate frontend `npm install` is required.

### 2. Bootstrap CDK (First Time Only)

If this is your first time using CDK in this AWS account/region:
```bash
cd infra-cdk
npx cdk bootstrap
```

### 3. Deploy Infrastructure

Build and deploy the complete stack:
```bash
cd infra-cdk
npm run build
npx cdk deploy --all
```

The deployment will:
1. Create a Cognito User Pool for authentication
2. Build and push the agent container to ECR
3. Create the AgentCore runtime
4. Build and deploy the React frontend to S3
5. Set up CloudFront distribution for the frontend

**Note**: The deployment takes approximately 10-15 minutes due to container building and AgentCore setup.

### 4. Create a Cognito User

**If you provided `admin_user_email` in config:**
- Check your email for temporary password
- Sign in and change password on first login

**If you didn't provide email:**
1. Go to the [AWS Cognito Console](https://console.aws.amazon.com/cognito/)
2. Find your User Pool (named `{stack_name_base}-user-pool`)
3. Click on the User Pool
4. Go to "Users" tab
5. Click "Create user"
6. Fill in the user details:
   - **Username**: Your desired username
   - **Email**: Your email address
   - **Temporary password**: Create a temporary password
   - **Mark email as verified**: Check this box
7. Click "Create user"

### 5. Access the Application

1. The deployment outputs will show the CloudFront URL
2. Open the URL in your browser
3. Sign in with the Cognito user you created
4. You'll be prompted to change your temporary password on first login

## Post-Deployment

### Updating the Application

To update the frontend code:
```bash
cd infra
npx cdk deploy --all --hotswap
```

To update the backend agent:
```bash
cd infra
npm run build
npx cdk deploy --all
```

### Custom Domain (Optional)

To use a custom domain:

1. Create an ACM certificate in `us-east-1` region
2. Update `infra-cdk/config.yaml` with your domain and certificate ARN
3. Redeploy: `npm run build && npx cdk deploy --all`
4. Update your DNS to point to the CloudFront distribution

### Monitoring and Logs

- **Frontend logs**: Check CloudFront access logs
- **Backend logs**: Check CloudWatch logs for the AgentCore runtime
- **Build logs**: Check CodeBuild project logs for container builds

## Cleanup

To remove all resources:
```bash
cd infra-cdk
npx cdk destroy --all
```

**Warning**: This will delete all data including S3 buckets and ECR images.

## Troubleshooting

### Common Issues

1. **"Agent Runtime ARN not configured"**
   - Ensure the backend stack deployed successfully
   - Check that SSM parameters were created correctly

2. **Authentication errors**
   - Verify you created a Cognito user
   - Check that the user's email is verified

3. **Build failures**
   - Check CodeBuild logs in the AWS Console
   - Ensure your agent code in `patterns/` is valid

4. **Permission errors**
   - Verify your AWS credentials have sufficient permissions
   - Check IAM roles created by the stack

### Getting Help

- Check CloudWatch logs for detailed error messages
- Review the CDK deployment output for any warnings
- Ensure all prerequisites are met

## Security Considerations

- The Cognito User Pool is configured with strong password policies
- All communication uses HTTPS via CloudFront
- AgentCore runtime uses JWT authentication
- IAM roles follow least-privilege principles

For production deployments, consider:
- Enabling MFA on Cognito users
- Setting up custom domains with your own certificates
- Configuring additional monitoring and alerting
- Implementing backup strategies for any persistent data
