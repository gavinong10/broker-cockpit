from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    internal_api_token: str = "dev-token"
    ib_gateway_host: str = "ib-gateway"
    ib_gateway_port: int = 4004
    ib_client_id: int = 11
    # Master switch for IBKR connectivity. False = don't run the reconnect
    # loop at all (e.g. while the paper account is pending activation);
    # health reports gateway "disabled" instead of "down".
    ib_enabled: bool = True
    discord_webhook_url: str = ""
    rh_session_file: str = "/secrets/rh-session.pickle"

settings = Settings()  # reads env vars
