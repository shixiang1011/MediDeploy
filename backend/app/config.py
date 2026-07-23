from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")
    database_url: str = "mysql+pymysql://spmp:spmp@mysql:3306/spmp"
    secret_key: str = "development-key-must-be-replaced"
    packages_dir: str = "/opt/spmp/packages"
    ansible_dir: str = "/opt/spmp/ansible"
    spmp_bootstrap_admin: str = "admin"
    spmp_bootstrap_password: str = "ChangeMeImmediately!"
    task_poll_seconds: int = 3


@lru_cache
def settings() -> Settings:
    return Settings()
