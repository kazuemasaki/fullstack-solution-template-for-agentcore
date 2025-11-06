# GenAIID AgentCore Starter Pack (GASP)

The GenAIID AgentCore Starter Pack (GASP) is a starter project repository that enables users (delivery scientists and engineers) to quickly deploy a secured, web-accessible React frontend connected to an AgentCore backend. Its purpose is to accelerate customer engagements from weeks to days by handling the undifferentiated heavy lifting of infrastructure setup and to enable vibe-coding style development on top.

GASP is designed with security and vibe-codability as primary tenets. Best practices and knowledge from experts are codified in _documentation_ in this repository rather than in _code_. By including this documentation in an AI coding assistant's context, or by instructing the AI coding assistant to leverage best practices and code snippets found in the documentation, delivery scientists and developers can quickly vibe-build AgentCore applications for any use case. AI coding assistants can be used to fully customize the frontend and the cdk infrastructure, enabling scientists to focus the areas where their knowledge is most impactful: the actual prompt engineering and GenAI implementation details.

With GASP as a starting point and development framework, delivery scientists and engineers will accelerate their development process and deliver production quality AgentCore code following architecture and security best practices without having to learn any frontend or infrastructure (cdk) code.

## GASP User Setup

If you are a delivery scientist or engineer who wants to use GASP to build a full stack application, this is the section for you.

TODO: write this section, including stuff like:

- describe how to set up their coding assistant with the right context and/or recommended MCP servers, make sure it describes the method for all common assistants (Q CLI, Cline, Kiro at a minimum).
- start by forking this repo
- recommend looking at the samples repository and optionally cloning one or two if it has characteristics that align with what they are trying to do
- deploy GASP out-of-the-box to make sure that works
- point users towards development best practice READMEs which e.g. explain to deploy the UI locally for quick UI development

## GASP Baseline System

GASP comes deployable out-of-the-box with a fully functioning application. This application represents a basic multi-turn chat conversation use case where the backend agent has access to some basic tools. **Do not let this deter you, even if your use case is entirely different! If your application requires AgentCore, customizing GASP to any use case is extremely straightforward through vibe coding.**

### Architecture

![Architecture Diagram](docs/img/GASP-architecture-20251029.png)
The out-of-the-box architecture is shown above. 

### Tech Stack

- **Frontend**: React with TypeScript, Vite build system, Cloudscape Design System
- **Agent Providers**: Multiple agent providers supported (Strands, LangGraph, etc.) running within AgentCore Runtime
- **Authentication**: AWS Cognito User Pool with OAuth support for easy swapping out Cognito
- **Infrastructure**: CDK deployment with S3 static hosting, CloudFront distribution, and AgentCore
- **Styling**: Dark/Light theme support

### Features

#### Authentication
- Cognito User Pool with email/username sign-in
- OAuth support with authorization code flow
- Secure password policy
- Email verification

#### Frontend
- Cloudscape Design System components
- Dark/Light theme toggle
- Responsive design
- SPA routing with React Router

#### Infrastructure
- S3 static website hosting
- CloudFront CDN with HTTPS
- Origin Access Control (OAC) for security
- Automatic deployment pipeline

## Deployment

The GASP system is deployed using AWS CDK. See the [deployment README](docs/DEPLOYMENT.md) for detailed instructions on how to deploy GASP into an AWS account.

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
├── infra-cdk/               # CDK infrastructure code
│   ├── lib/                # CDK stack definitions
│   │   └── utils/          # Utility functions
│   ├── bin/                # CDK app entry point
│   ├── config.yaml         # Configuration
│   ├── package.json
│   └── tsconfig.json
├── patterns/               # Agent pattern implementations
│   └── strands-single-agent/ # Basic strands agent pattern
│       ├── basic_agent.py  # Agent implementation
│       ├── requirements.txt # Agent dependencies
│       └── Dockerfile      # Container configuration
├── docs/                   # Documentation
└── README.md
```




## License

This project is licensed under the MIT-0 License.
