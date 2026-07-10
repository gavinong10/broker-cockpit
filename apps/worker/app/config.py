from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    internal_api_token: str = "dev-token"
    ib_gateway_host: str = "ib-gateway"
    ib_gateway_port: int = 4004
    ib_client_id: int = 11
    discord_webhook_url: str = ""

settings = Settings()  # reads env vars
