# GenAIID AgentCore Starter Pack

The GenAIID AgentCore Starter Pack (GASP) is a starter project repository that enables developers to use CDK to quickly deploy a secured, web-accessible React frontend connected to an AgentCore backend. Its purpose is to accelerate customer engagements from weeks to days by handling the undifferentiated heavy lifting of infrastructure setup and to enable vibe-coding style development on top.

## Architecture

![Architecture Diagram](docs/img/GASP-architecture-20251023.png)

## What's Included

- **Frontend**: React with TypeScript, Vite build system, Cloudscape Design System
- **Backend**: AWS Bedrock AgentCore runtime with configurable agent patterns
- **Authentication**: AWS Cognito User Pool with OAuth support
- **Infrastructure**: CDK deployment with S3 static hosting, CloudFront distribution, and AgentCore runtime
- **Styling**: Dark/Light theme support
- **Agent Patterns**: Pluggable agent implementations (currently includes strands-single-agent)

## Prerequisites

- Node.js 18+ 
- Python 3.9+
- AWS CLI configured
- AWS CDK CLI installed (`npm install -g aws-cdk`)

## Quick Start

### 1. Install Dependencies

Frontend:
```bash
cd frontend
npm install
```

Infrastructure:
```bash
cd infra
pip install -r requirements.txt
```

### 2. Deploy Infrastructure

```bash
cd infra
cdk bootstrap  # Only needed once per AWS account/region
cdk deploy
```

### 3. Local Development

```bash
cd frontend
npm run dev
```

The application will be available at `http://localhost:5173`

## Project Structure

```
genaiid-agentcore-starter-pack/
├── frontend/                 # React frontend application
│   ├── src/
│   │   ├── components/      # React components
│   │   ├── pages/          # Page components
│   │   ├── common/         # Utilities and constants
│   │   └── styles/         # SCSS styles
│   ├── public/             # Static assets
│   └── package.json
├── infra/                   # CDK infrastructure code
│   ├── stacks/             # CDK stack definitions
│   ├── utils/              # Utility functions
│   ├── custom_constructs/  # Custom CDK constructs
│   ├── app.py              # CDK app entry point
│   ├── config.yaml         # Configuration
│   └── requirements.txt
├── patterns/               # Agent pattern implementations
│   └── strands-single-agent/ # Basic strands agent pattern
│       ├── basic_agent.py  # Agent implementation
│       ├── requirements.txt # Agent dependencies
│       └── Dockerfile      # Container configuration
└── README.md
│   ├── config.yaml         # Configuration
│   └── requirements.txt
└── README.md
```

## Configuration

Edit `infra/config.yaml` to customize:

- Stack name
- Custom domain (optional)
- Certificate ARN (optional)
- Backend agent pattern selection

## Features

### Authentication
- Cognito User Pool with email/username sign-in
- OAuth support with authorization code flow
- Secure password policy
- Email verification

### Frontend
- Cloudscape Design System components
- Dark/Light theme toggle
- Responsive design
- SPA routing with React Router

### Infrastructure
- S3 static website hosting
- CloudFront CDN with HTTPS
- Origin Access Control (OAC) for security
- Automatic deployment pipeline

## Development

### Adding New Pages

1. Create a new component in `frontend/src/pages/`
2. Add the route in `frontend/src/app.tsx`
3. Update navigation if needed

### Customizing Styles

- Global styles: `frontend/src/styles/app.scss`
- Theme switching: `frontend/src/common/helpers/storage-helper.ts`

### Infrastructure Changes

- Modify stacks in `infra/stacks/`
- Deploy changes with `cdk deploy`

## Deployment

The CDK deployment will:

1. Create a Cognito User Pool and Client
2. Set up S3 bucket for static hosting
3. Configure CloudFront distribution
4. Build and deploy the React application
5. Generate `aws-exports.json` with configuration

## Cleanup

To remove all resources:

```bash
cd infra
cdk destroy
```

## Next Steps

This starter pack provides a foundation for building full-stack applications. Consider adding:

- API Gateway and Lambda functions for backend logic
- DynamoDB for data storage
- Additional Cognito features (MFA, custom attributes)
- CI/CD pipeline
- Custom domain and SSL certificate
- Monitoring and logging

## License

This project is licensed under the MIT-0 License.
