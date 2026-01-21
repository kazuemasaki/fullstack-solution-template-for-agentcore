# Claude Code ガイドライン

## 開発方針

- **MCPサーバーを活用する（LLMの知識に頼らない）**
  - AWSサービスやTerraformの情報は、LLMの学習データではなく、MCPサーバーから最新の正確な情報を取得すること
  - ドキュメントやベストプラクティスは必ずMCPツールで検索・参照する

## 登録済みMCPサーバーとユースケース

### 1. aws-knowledge-mcp-server
AWSの公式ドキュメントと知識ベースにアクセスするサーバー

**ユースケース:**
- AWSサービスのドキュメント検索・参照
- AWSサービスのリージョン対応状況の確認
- AWSアーキテクチャの推奨事項の取得

**主要ツール:**
- `aws___search_documentation` - ドキュメント検索
- `aws___read_documentation` - ドキュメント参照
- `aws___recommend` - 推奨事項の取得
- `aws___list_regions` / `aws___get_regional_availability` - リージョン情報

### 2. terraform (HashiCorp公式)
Terraform Registryとの連携サーバー

**ユースケース:**
- Terraformプロバイダー・モジュールの検索
- プロバイダーの最新バージョン確認
- モジュールの詳細情報取得

**主要ツール:**
- `search_providers` / `get_provider_details` - プロバイダー検索
- `search_modules` / `get_module_details` - モジュール検索
- `get_latest_provider_version` / `get_latest_module_version` - バージョン確認

### 3. awslabs.terraform-mcp-server
AWS向けTerraform開発に特化したサーバー

**ユースケース:**
- AWS/AWSCCプロバイダーのドキュメント検索
- AWS-IAモジュール（Bedrock, OpenSearch等）の検索
- Terraformコマンドの実行（validate, plan, apply等）

**主要ツール:**
- `SearchAwsProviderDocs` / `SearchAwsccProviderDocs` - AWSプロバイダードキュメント
- `SearchSpecificAwsIaModules` - AWS-IA専用モジュール検索
- `ExecuteTerraformCommand` - Terraformコマンド実行
- `RunCheckovScan` - セキュリティスキャン

### 4. agentcore
Amazon Bedrock AgentCoreの管理サーバー

**ユースケース:**
- AgentCoreランタイムの管理
- AgentCoreメモリの管理
- AgentCoreゲートウェイの管理
- AgentCoreドキュメントの検索・参照

**主要ツール:**
- `manage_agentcore_runtime` - ランタイム管理
- `manage_agentcore_memory` - メモリ管理
- `manage_agentcore_gateway` - ゲートウェイ管理
- `search_agentcore_docs` / `fetch_agentcore_doc` - ドキュメント参照

### 5. strands
Strands Agentsフレームワークのドキュメントサーバー

**ユースケース:**
- Strands Agentsの使い方・実装方法の参照
- エージェント開発のベストプラクティス確認

**主要ツール:**
- `search_docs` - ドキュメント検索
- `fetch_doc` - ドキュメント取得
