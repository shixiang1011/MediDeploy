from pathlib import PurePosixPath

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import DeploymentMode, PackageType, Role


SUPPORTED_COMPONENTS = {
    "redis",
    "nacos",
    "minio",
    "rabbitmq",
    "kafka",
    "elasticsearch",
    "nginx",
    "nexus",
}


class LoginRequest(BaseModel):
    username: str
    password: str


class HostConnection(BaseModel):
    address: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(min_length=1, max_length=100)
    ssh_password: str = Field(min_length=1)
    use_sudo: bool = False
    sudo_password: str | None = None


class HostCreate(HostConnection):
    name: str = Field(min_length=1, max_length=100)


class PackageMetadata(BaseModel):
    component: str
    version: str = Field(min_length=1, max_length=80)
    package_type: PackageType
    architecture: str = "x86_64"
    description: str = Field(default="", max_length=500)

    @field_validator("component")
    @classmethod
    def supported_component(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in SUPPORTED_COMPONENTS:
            raise ValueError("暂不支持该中间件类型")
        return normalized

    @field_validator("architecture")
    @classmethod
    def supported_architecture(cls, value: str) -> str:
        if value != "x86_64":
            raise ValueError("当前仅支持 x86_64，ARM64 接口将在后续版本开放")
        return value


class RedisInstance(BaseModel):
    host_id: str
    port: int = Field(default=6379, ge=1024, le=55535)
    install_dir: str = "/opt/middleware/redis"
    data_dir: str = "/data/redis"
    log_dir: str = "/var/log/middleware/redis"
    config_dir: str = "/etc/middleware/redis"
    custom_redis_conf: str | None = None

    @field_validator("install_dir", "data_dir", "log_dir", "config_dir")
    @classmethod
    def absolute_safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not value.startswith("/") or value == "/" or ".." in path.parts:
            raise ValueError("目录必须是非根目录的绝对路径，且不能包含 ..")
        return value.rstrip("/")


class RedisDeploymentConfig(BaseModel):
    mode: DeploymentMode
    instances: list[RedisInstance] = Field(min_length=1)
    primary_count: int = Field(default=1, ge=1, le=100)
    replicas_per_primary: int = Field(default=0, ge=0, le=10)
    redis_password: str = Field(min_length=8)
    maxmemory: str | None = None
    maxmemory_policy: str = "noeviction"
    appendonly: bool = True

    @model_validator(mode="after")
    def validate_topology(self):
        endpoints = []
        for item in self.instances:
            endpoints.append((item.host_id, item.port))
            if self.mode == DeploymentMode.CLUSTER:
                endpoints.append((item.host_id, item.port + 10000))
        if len(endpoints) != len(set(endpoints)):
            raise ValueError("同一服务器的 Redis 端口或 Cluster Bus 端口发生冲突")
        paths_by_host: dict[str, list[PurePosixPath]] = {}
        for item in self.instances:
            paths_by_host.setdefault(item.host_id, []).extend(
                PurePosixPath(value)
                for value in (
                    item.install_dir,
                    item.data_dir,
                    item.log_dir,
                    item.config_dir,
                )
            )
        for paths in paths_by_host.values():
            for index, left in enumerate(paths):
                for right in paths[index + 1 :]:
                    if left == right or left in right.parents or right in left.parents:
                        raise ValueError(
                            f"同一服务器上的任务目录不能重复或互相嵌套：{left} 与 {right}"
                        )
        if self.mode == DeploymentMode.STANDALONE:
            if len(self.instances) != 1:
                raise ValueError("Redis 单机模式必须且只能配置一个实例")
            self.primary_count = 1
            self.replicas_per_primary = 0
        else:
            if self.primary_count < 3:
                raise ValueError("Redis Cluster 至少需要 3 个主节点")
            expected = self.primary_count * (self.replicas_per_primary + 1)
            if len(self.instances) != expected:
                raise ValueError(f"当前拓扑需要 {expected} 个实例，实际配置 {len(self.instances)} 个")
        return self


class DeploymentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    component: str
    package_id: str
    mode: DeploymentMode
    config: RedisDeploymentConfig

    @model_validator(mode="after")
    def consistent_component_and_mode(self):
        if self.component != "redis":
            raise ValueError("当前版本仅实现 Redis 部署")
        if self.mode != self.config.mode:
            raise ValueError("部署模式与配置不一致")
        return self


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
