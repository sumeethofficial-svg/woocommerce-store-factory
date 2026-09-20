import secrets
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "WooCommerce Store Factory"
    database_url: str = "sqlite:///./factory.db"
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    bcrypt_rounds: int = 12
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"
    log_json: bool = False

    max_stores_per_user: int = 3
    max_storage_gi_per_user: int = 5
    max_store_storage_gi: int = 5
    default_store_storage_gi: int = 1
    worker_threads: int = 4

    store_base_domain: str = "localhost"
    store_public_port: int = 80
    ingress_enabled: bool = True
    ingress_class: str = "nginx"
    storage_class: str | None = None
    mysql_storage_gi: int = 1
    mysql_image: str = "mysql:8.0"
    wordpress_image: str = "wordpress:latest"
    wpcli_image: str = "wordpress:cli"
    sample_products: bool = False
    store_currency: str = "INR"
    store_country: str = "IN:KA"
    razorpay_key_id: str | None = None
    razorpay_key_secret: SecretStr | None = None

    poll_interval_seconds: float = 2.0
    mysql_ready_timeout: int = 300
    wordpress_ready_timeout: int = 420
    init_timeout: int = 900
    namespace_delete_timeout: int = 180

    @model_validator(mode="after")
    def _require_razorpay_test_keys(self) -> "Settings":
        if self.razorpay_key_id and not self.razorpay_key_id.startswith("rzp_test_"):
            raise ValueError("RAZORPAY_KEY_ID must be a test key (rzp_test_...). Live keys are not allowed.")
        return self

    @property
    def razorpay_enabled(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret and self.razorpay_key_secret.get_secret_value())

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
