from functools import lru_cache
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

    # ChromaDB
    chroma_host: str = "chromadb"
    chroma_port: int = 8000
    # Fallback for local dev without Docker
    chroma_persist_dir: str = "./chroma_db"
    chroma_use_http: bool = True

    # Venue APIs
    geoapify_api_key: str = ""
    foursquare_api_key: str = ""

    # MCP-Tools server
    mcp_server_url: str = "http://localhost:9000"
    mcp_caller_id: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def is_openai_configured(self) -> bool:
        return bool(self.azure_openai_api_key and self.azure_openai_endpoint)


@lru_cache
def get_settings() -> Settings:
    return Settings()
