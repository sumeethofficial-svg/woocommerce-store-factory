import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    StringConstraints,
    computed_field,
    field_validator,
)

from app.models import StoreStatus

STORE_NAME_PATTERN = r"^[a-z][a-z0-9-]{1,28}[a-z0-9]$"
USERNAME_PATTERN = r"^[a-z0-9_]{3,32}$"
ADMIN_PASSWORD_PATTERN = re.compile(r"^[\x21-\x7e]{8,64}$")
MAX_PRODUCTS = 20


class Registration(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: EmailStr
    password: str = Field(min_length=8, max_length=64)

    @field_validator("username")
    @classmethod
    def _normalize_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(USERNAME_PATTERN, value):
            raise ValueError("Username may contain lowercase letters, numbers and underscores")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProductIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    price: Decimal = Field(ge=0, le=Decimal("10000000"), decimal_places=2)
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""

    def as_record(self) -> dict[str, str]:
        return {"name": self.name, "price": format(self.price, "f"), "description": self.description}


class StoreCreate(BaseModel):
    name: str = Field(pattern=STORE_NAME_PATTERN, min_length=3, max_length=30)
    admin_password: SecretStr | None = None
    storage_gi: int = Field(default=1, ge=1)
    products: list[ProductIn] = Field(default_factory=list, max_length=MAX_PRODUCTS)

    @field_validator("admin_password")
    @classmethod
    def _check_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not ADMIN_PASSWORD_PATTERN.match(value.get_secret_value()):
            raise ValueError("Admin password must be 8 to 64 printable characters without spaces")
        return value


class _Timestamped(BaseModel):
    @field_validator("created_at", "updated_at", check_fields=False)
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)


class StoreOut(_Timestamped):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    owner: str = Field(validation_alias="owner_username")
    namespace: str
    status: StoreStatus
    storage_gi: int
    url: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def admin_url(self) -> str | None:
        return f"{self.url}/wp-admin/" if self.url else None


class StoreEventOut(_Timestamped):
    model_config = ConfigDict(from_attributes=True)

    id: int
    level: str
    message: str
    created_at: datetime


class AdminCredentials(BaseModel):
    username: str
    password: str
    admin_url: str | None


class Quota(BaseModel):
    used: int
    limit: int


class UsageOut(BaseModel):
    stores: Quota
    storage_gi: Quota
    max_store_storage_gi: int
    mysql_storage_gi: int
    online_payments: bool
