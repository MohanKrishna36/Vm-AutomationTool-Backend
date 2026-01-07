from pydantic import BaseSettings

class Settings(BaseSettings):
    app_name: str = "VM Automation Backend"
    debug: bool = True

settings = Settings()