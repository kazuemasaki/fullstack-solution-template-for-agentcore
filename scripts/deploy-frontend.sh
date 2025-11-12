#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration - These will be populated from CDK outputs
STACK_NAME=""
APP_ID=""
DEPLOYMENT_BUCKET=""

# Defaults
BRANCH_NAME=main
S3_KEY=amplify-deploy-$(date +%s).zip
NEXT_BUILD_DIR=build

# Helper functions
log_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1" >&2
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Get configuration from CDK stack outputs
get_cdk_outputs() {
    if [ -z "$STACK_NAME" ]; then
        # Try to get stack name from config.yaml
        CONFIG_FILE="$PROJECT_ROOT/infra-cdk/config.yaml"

        if [ -f "$CONFIG_FILE" ]; then
            STACK_NAME=$(grep "stack_name_base:" "$CONFIG_FILE" | awk '{print $2}' | tr -d '"' || echo "")
        fi

        if [ -z "$STACK_NAME" ]; then
            log_error "STACK_NAME environment variable is required or config.yaml not found"
            log_info "Usage: STACK_NAME=your-stack-name ./deploy-frontend.sh"
            exit 1
        fi
    fi

    log_info "Fetching configuration from CDK stack: $STACK_NAME"

    # Get all the configuration from CDK outputs
    APP_ID=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='AmplifyAppId'].OutputValue" \
        --output text 2>/dev/null)

    DEPLOYMENT_BUCKET=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='StagingBucketName'].OutputValue" \
        --output text 2>/dev/null)

    FEEDBACK_API_URL=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='FeedbackApiUrl'].OutputValue" \
        --output text 2>/dev/null)

    # Validate all required values
    if [ -z "$APP_ID" ] || [ "$APP_ID" = "None" ]; then
        log_error "Could not find Amplify App ID in stack outputs"
        exit 1
    fi

    if [ -z "$DEPLOYMENT_BUCKET" ] || [ "$DEPLOYMENT_BUCKET" = "None" ]; then
        log_error "Could not find Staging Bucket Name in stack outputs"
        exit 1
    fi

    if [ -z "$FEEDBACK_API_URL" ] || [ "$FEEDBACK_API_URL" = "None" ]; then
        log_error "Could not find Feedback API URL in stack outputs"
        exit 1
    fi

    log_success "✓ App ID: $APP_ID"
    log_success "✓ Staging Bucket: $DEPLOYMENT_BUCKET"
    log_success "✓ Feedback API URL: $FEEDBACK_API_URL"
}

cleanup() {
    if [ -f "amplify-deploy.zip" ]; then
        rm -f amplify-deploy.zip
        log_info "Cleaned up local zip file"
    fi
}

trap cleanup EXIT

# Find and change to project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_ROOT/frontend/package.json" ]; then
    cd "$PROJECT_ROOT/frontend"
    log_info "Changed to frontend directory: $(pwd)"
else
    log_error "Cannot find frontend/package.json. Make sure you're running from the correct directory."
    exit 1
fi

# Main execution starts here
log_info "🚀 Starting frontend deployment process..."
echo

# Validate prerequisites
log_info "Validating prerequisites..."
if ! command -v npm &> /dev/null; then
    log_error "npm is not installed"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    log_error "python3 is not installed"
    exit 1
fi

if ! command -v aws &> /dev/null; then
    log_error "AWS CLI is not installed"
    exit 1
fi

if ! command -v zip &> /dev/null; then
    log_error "zip is not installed"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    log_error "jq is not installed"
    exit 1
fi

# Get CDK configuration
get_cdk_outputs

# Generate fresh aws-exports.json from CDK stack outputs
log_info "Generating aws-exports.json from CDK stack outputs..."
AWS_EXPORTS_FILE="public/aws-exports.json"
GENERATOR_SCRIPT="$SCRIPT_DIR/post-deploy.py"

if python3 "$GENERATOR_SCRIPT" "$STACK_NAME"; then
    log_success "Generated aws-exports.json"
else
    log_error "Failed to generate aws-exports.json"
    log_info "Make sure you've deployed the CDK stack first with: cdk deploy"
    exit 1
fi

# Check and install dependencies if needed
log_info "Checking frontend dependencies..."
if [ ! -d "node_modules" ] || [ "package.json" -nt "node_modules" ]; then
    log_info "Installing/updating dependencies..."
    if npm install; then
        log_success "Dependencies installed successfully"
    else
        log_error "Failed to install dependencies"
        exit 1
    fi
else
    log_success "Dependencies are up to date"
fi

# Build Next.js app
log_info "Building Next.js app..."
if npm run build; then
    log_success "Build completed successfully"
else
    log_error "Build failed"
    exit 1
fi

# Verify build directory exists
if [ ! -d "$NEXT_BUILD_DIR" ]; then
    log_error "Build directory '$NEXT_BUILD_DIR' not found"
    exit 1
fi

# Copy aws-exports.json to build directory
log_info "Adding aws-exports.json to build..."
if [ -f "$AWS_EXPORTS_FILE" ]; then
    cp "$AWS_EXPORTS_FILE" "$NEXT_BUILD_DIR/"
    log_success "Added aws-exports.json to build directory"
else
    log_error "aws-exports.json not found"
    exit 1
fi

# Create deployment zip
log_info "Creating deployment package..."
if (cd "$NEXT_BUILD_DIR" && zip -r ../amplify-deploy.zip . -q); then
    ZIP_SIZE=$(ls -lah amplify-deploy.zip | awk '{print $5}')
    log_success "Package created (${ZIP_SIZE})"
else
    log_error "Failed to create deployment package"
    exit 1
fi

# Upload to S3
log_info "Uploading to S3 (s3://$DEPLOYMENT_BUCKET/$S3_KEY)..."
if aws s3 cp amplify-deploy.zip "s3://$DEPLOYMENT_BUCKET/$S3_KEY" --no-progress; then
    log_success "Upload completed"
else
    log_error "S3 upload failed"
    exit 1
fi

# Start Amplify deployment
log_info "Starting Amplify deployment..."
log_info "Command: aws amplify start-deployment --app-id $APP_ID --branch-name $BRANCH_NAME --source-url s3://$DEPLOYMENT_BUCKET/$S3_KEY"

DEPLOYMENT_OUTPUT=$(aws amplify start-deployment \
    --app-id "$APP_ID" \
    --branch-name "$BRANCH_NAME" \
    --source-url "s3://$DEPLOYMENT_BUCKET/$S3_KEY" \
    --output json 2>&1)

echo "---------- Amplify deployment -----------"
echo "$DEPLOYMENT_OUTPUT"
echo "-----------------------------------------"

if [ $? -eq 0 ]; then
    JOB_ID=$(echo "$DEPLOYMENT_OUTPUT" | jq -r '.jobSummary.jobId')

    # Get app URL
    APP_URL=$(aws amplify get-app --app-id "$APP_ID" --query 'app.defaultDomain' --output text)

    log_success "Deployment initiated successfully"
    echo
    log_info "Job ID: $JOB_ID"

    # Poll deployment status
    log_info "Monitoring deployment status..."
    while true; do
        STATUS=$(aws amplify get-job --app-id "$APP_ID" --branch-name "$BRANCH_NAME" --job-id "$JOB_ID" --output json | jq -r '.job.summary.status')

        echo "  Status: $STATUS"

        case $STATUS in
            "SUCCEED")
                log_success "Deployment completed successfully!"
                break
                ;;
            "FAILED")
                log_error "Deployment failed"
                exit 1
                ;;
            "CANCELLED")
                log_error "Deployment was cancelled"
                exit 1
                ;;
            *)
                sleep 10
                ;;
        esac
    done

    echo
    log_info "S3 Package: s3://$DEPLOYMENT_BUCKET/$S3_KEY"
    log_info "Console: https://console.aws.amazon.com/amplify/apps"
    log_info "App URL: https://$BRANCH_NAME.$APP_URL"
else
    log_error "Amplify deployment failed"
    echo "$DEPLOYMENT_OUTPUT"
    exit 1
fi