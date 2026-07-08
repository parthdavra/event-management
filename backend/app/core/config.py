import json
import os
from functools import lru_cache

import boto3
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Application
    app_name: str = "AI Event Management API"
    debug: bool = False

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/event_mgmt"

    # JWT
    secret_key: str = ""
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Azure OpenAI
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-02-01"
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_embedding_deployment: str = "text-embedding-ada-002"

    # OpenSearch (cloud vector DB)
    opensearch_host: str = ""
    opensearch_port: int = 443
    opensearch_username: str = ""
    opensearch_password: str = ""
    opensearch_index_name: str = "event_management_vectors"

    # Venue APIs
    geoapify_api_key: str = ""
    foursquare_api_key: str = ""

    # MCP-Tools server
    mcp_server_url: str = "http://localhost:9000"
    mcp_caller_id: str = ""

    # Langfuse
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"

    # AWS (CloudWatch observability metrics)
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_session_token: str = ""
    aws_region: str = "eu-west-2"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def is_openai_configured(self) -> bool:
        return bool(self.azure_openai_api_key and self.azure_openai_endpoint)


def _fetch_secrets_from_aws() -> dict:
    """
    Fetch the consolidated app secret from AWS Secrets Manager.

    Only the AWS identity itself (AWS_ACCESS_KEY_ID/SECRET/SESSION_TOKEN/REGION)
    comes from plain environment variables — everything else the app needs
    (DB URL, JWT key, Azure OpenAI, OpenSearch, venue APIs, MCP, Langfuse)
    lives in this one secret. No local fallback: if this call fails, the app
    fails to start rather than silently running on stale/missing config.
    """
    region = os.environ.get("AWS_REGION", "eu-west-2")
    secret_name = os.environ.get("AWS_SECRETS_MANAGER_SECRET_NAME", "dstrmaysam-evmgmt-secrets")
    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response["SecretString"])


@lru_cache
def get_settings() -> Settings:
    return Settings(**_fetch_secrets_from_aws())
