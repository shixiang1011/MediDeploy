import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def uid() -> str:
    return str(uuid.uuid4())


class Role(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    TENANT_ADMIN = "tenant_admin"
    OPERATOR = "operator"
    AUDITOR = "auditor"


class TaskStatus(str, enum.Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLBACK_QUEUED = "rollback_queued"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


class PackageType(str, enum.Enum):
    SOURCE = "source"
    BINARY = "binary"


class DeploymentMode(str, enum.Enum):
    STANDALONE = "standalone"
    CLUSTER = "cluster"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.OPERATOR, nullable=False)
    auth_source: Mapped[str] = mapped_column(String(20), default="local", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Host(Base, TimestampMixin):
    __tablename__ = "hosts"
    __table_args__ = (UniqueConstraint("tenant_id", "address", "ssh_port", name="uq_host_endpoint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    ssh_port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    ssh_user: Mapped[str] = mapped_column(String(100), nullable=False)
    ssh_password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    use_sudo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sudo_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    os_family: Mapped[str] = mapped_column(String(50), nullable=False)
    os_version: Mapped[str] = mapped_column(String(100), nullable=False)
    architecture: Mapped[str] = mapped_column(String(20), default="x86_64", nullable=False)
    facts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    connection_status: Mapped[str] = mapped_column(String(20), default="verified", nullable=False)
    last_tested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Package(Base, TimestampMixin):
    __tablename__ = "packages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    component: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    package_type: Mapped[PackageType] = mapped_column(Enum(PackageType), nullable=False)
    architecture: Mapped[str] = mapped_column(String(20), default="x86_64", nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)


class Deployment(Base, TimestampMixin):
    __tablename__ = "deployments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    component: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mode: Mapped[DeploymentMode] = mapped_column(Enum(DeploymentMode), nullable=False)
    package_id: Mapped[str] = mapped_column(ForeignKey("packages.id"), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), default=TaskStatus.DRAFT, nullable=False, index=True
    )
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    rollback_result: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaskLog(Base):
    __tablename__ = "task_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    level: Mapped[str] = mapped_column(String(10), default="INFO", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class LdapConfig(Base, TimestampMixin):
    __tablename__ = "ldap_configs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    server_url: Mapped[str] = mapped_column(String(255), nullable=False)
    base_dn: Mapped[str] = mapped_column(String(255), nullable=False)
    bind_dn: Mapped[str] = mapped_column(String(255), nullable=False)
    bind_password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    user_filter: Mapped[str] = mapped_column(String(255), default="(uid={username})", nullable=False)
    group_role_mapping: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
