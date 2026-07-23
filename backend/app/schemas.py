from pydantic import BaseModel, Field, field_validator
from app.models import Role


class LoginRequest(BaseModel):
    username: str
    password: str


class HostCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(min_length=1, max_length=100)
    ssh_private_key: str = Field(min_length=1)
    os_family: str
    os_version: str
    architecture: str = "x86_64"


class RedisConfig(BaseModel):
    node_ids: list[str] = Field(min_length=3)
    replicas: int = Field(default=1, ge=0, le=10)
    redis_port: int = Field(default=6379, ge=1024, le=65535)
    install_dir: str = "/opt/middleware/redis"
    data_dir: str = "/data/redis"
    log_dir: str = "/var/log/middleware/redis"
    config_dir: str = "/etc/middleware/redis"
    redis_password: str = Field(min_length=8)
    maxmemory: str | None = None
    maxmemory_policy: str = "noeviction"
    appendonly: bool = True
    custom_redis_conf: str | None = None

    @field_validator("install_dir", "data_dir", "log_dir", "config_dir")
    @classmethod
    def absolute_safe_path(cls, value: str) -> str:
        if not value.startswith("/") or value == "/" or ".." in value.split("/"):
            raise ValueError("目录必须是非根目录的绝对路径，且不能含 ..")
        return value.rstrip("/")

    def validate_cluster_shape(self) -> None:
        if len(set(self.node_ids)) != len(self.node_ids):
            raise ValueError("Redis 节点不能重复")
        if len(self.node_ids) % (self.replicas + 1) != 0:
            raise ValueError("节点数必须能被 (副本数 + 1) 整除")


class DeploymentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    package_id: str
    config: RedisConfig


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)


class UserCreate(BaseModel):
    username: str
    password: str
    tenant_id: str | None = None
    role: Role = Role.OPERATOR


class LdapConfigRequest(BaseModel):
    enabled: bool = False
    server_url: str
    base_dn: str
    bind_dn: str
    bind_password: str
    user_filter: str = "(uid={username})"
    group_role_mapping: dict[str, str] = {}
