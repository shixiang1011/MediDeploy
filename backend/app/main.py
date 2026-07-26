from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ldap3 import ALL, Connection, Server
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import engine, get_db
from app.host_probe import probe_host
from app.models import (
    AuditLog,
    Deployment,
    Host,
    LdapConfig,
    Package,
    PackageType,
    Role,
    TaskLog,
    TaskStatus,
    Tenant,
    User,
)
from app.schemas import (
    DeploymentCreate,
    HostConnection,
    HostCreate,
    LdapConfigRequest,
    LoginRequest,
    PackageMetadata,
    TenantCreate,
    UserCreate,
)
from app.report_generator import build_report, report_path_for
from app.security import decrypt, encrypt, password_hash, password_matches, token_for, token_subject


app = FastAPI(title="SP MediDeploy Platform", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
bearer = HTTPBearer()

COMPONENTS = [
    {"id": "redis", "name": "Redis", "available": True, "modes": ["standalone", "cluster"]},
    {"id": "nacos", "name": "Nacos", "available": False, "modes": ["standalone", "cluster"]},
    {"id": "minio", "name": "MinIO", "available": False, "modes": ["standalone", "cluster"]},
    {"id": "rabbitmq", "name": "RabbitMQ", "available": False, "modes": ["standalone", "cluster"]},
    {"id": "kafka", "name": "Kafka", "available": False, "modes": ["standalone", "cluster"]},
    {"id": "elasticsearch", "name": "Elasticsearch", "available": False, "modes": ["standalone", "cluster"]},
    {"id": "nginx", "name": "Nginx", "available": False, "modes": ["standalone", "cluster"]},
    {"id": "nexus", "name": "Nexus", "available": False, "modes": ["standalone"]},
]


@app.on_event("startup")
def bootstrap() -> None:
    with Session(engine) as db:
        tenant = db.scalar(select(Tenant).where(Tenant.name == "默认租户"))
        if not tenant:
            tenant = Tenant(name="默认租户")
            db.add(tenant)
            db.flush()
        admin = db.scalar(select(User).where(User.username == settings().spmp_bootstrap_admin))
        if not admin:
            admin = User(
                username=settings().spmp_bootstrap_admin,
                password_hash=password_hash(settings().spmp_bootstrap_password),
                role=Role.SUPER_ADMIN,
                tenant_id=tenant.id,
            )
            db.add(admin)
        elif not admin.tenant_id:
            admin.tenant_id = tenant.id
        db.commit()
    Path(settings().packages_dir).mkdir(parents=True, exist_ok=True)


def audit(
    db: Session,
    user: User | None,
    action: str,
    resource: str,
    resource_id: str | None,
    detail: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            tenant_id=user.tenant_id if user else None,
            user_id=user.id if user else None,
            action=action,
            resource_type=resource,
            resource_id=resource_id,
            detail=detail or {},
        )
    )


def current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, token_subject(credentials.credentials))
    if not user or not user.enabled:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    return user


def require(*roles: Role):
    def inner(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="当前角色没有此操作权限")
        return user

    return inner


def tenant_query(model, user: User):
    statement = select(model)
    if user.role != Role.SUPER_ADMIN:
        statement = statement.where(model.tenant_id == user.tenant_id)
    return statement


@app.get("/health")
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "SPMP"}


@app.get("/api/components")
def list_components(user: User = Depends(current_user)):
    return COMPONENTS


@app.post("/api/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if (
        user
        and user.enabled
        and user.auth_source == "local"
        and user.password_hash
        and password_matches(body.password, user.password_hash)
    ):
        audit(db, user, "login", "user", user.id)
        db.commit()
        return {"access_token": token_for(user.id), "user": user_view(user)}
    ldap = db.scalar(select(LdapConfig).where(LdapConfig.enabled.is_(True)))
    if not ldap:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    try:
        server = Server(ldap.server_url, get_info=ALL)
        service = Connection(
            server,
            user=ldap.bind_dn,
            password=decrypt(ldap.bind_password_encrypted),
            auto_bind=True,
        )
        found = service.search(
            ldap.base_dn,
            ldap.user_filter.format(username=body.username),
            attributes=["memberOf"],
        )
        if not found or not service.entries:
            raise ValueError("未找到 LDAP 用户")
        entry = service.entries[0]
        if not Connection(server, user=entry.entry_dn, password=body.password, auto_bind=True).bound:
            raise ValueError("LDAP 密码错误")
        groups = [str(value) for value in getattr(entry, "memberOf", [])]
        role = next(
            (
                Role(value)
                for group, value in ldap.group_role_mapping.items()
                if group in groups and value in Role._value2member_map_
            ),
            Role.OPERATOR,
        )
        if not user:
            default_tenant = db.scalar(select(Tenant).where(Tenant.name == "默认租户"))
            user = User(
                username=body.username,
                tenant_id=default_tenant.id if default_tenant else None,
                password_hash=None,
                role=role,
                auth_source="ldap",
            )
            db.add(user)
            db.flush()
        audit(db, user, "ldap_login", "user", user.id)
        db.commit()
        return {"access_token": token_for(user.id), "user": user_view(user)}
    except Exception:
        raise HTTPException(status_code=401, detail="LDAP 认证失败")


def user_view(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "tenant_id": user.tenant_id,
        "role": user.role.value,
        "auth_source": user.auth_source,
    }


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return user_view(user)


@app.post("/api/hosts/test")
def test_host_connection(
    body: HostConnection,
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)),
):
    try:
        facts = probe_host(body)
        return {"success": True, "message": "SSH、sudo、Python 3 和系统信息检查通过", "facts": facts}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"服务器连接测试失败：{str(exc)[-2000:]}")


@app.get("/api/hosts")
def list_hosts(
    q: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    statement = tenant_query(Host, user).where(Host.enabled.is_(True))
    if q and (term := q.strip()):
        pattern = f"%{term}%"
        statement = statement.where(
            or_(
                Host.name.ilike(pattern),
                Host.address.ilike(pattern),
                Host.ssh_user.ilike(pattern),
                Host.os_family.ilike(pattern),
                Host.os_version.ilike(pattern),
            )
        )
    return [
        host_view(item)
        for item in db.scalars(statement.order_by(Host.created_at.desc())).all()
    ]


@app.post("/api/hosts", status_code=201)
def create_host(
    body: HostCreate,
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)),
    db: Session = Depends(get_db),
):
    if not user.tenant_id:
        raise HTTPException(status_code=400, detail="当前用户未关联租户")
    try:
        facts = probe_host(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"保存前连接测试失败：{str(exc)[-2000:]}")
    host = db.scalar(
        select(Host).where(
            Host.tenant_id == user.tenant_id,
            Host.address == body.address,
            Host.ssh_port == body.ssh_port,
        )
    )
    restored = bool(host and not host.enabled)
    if host and host.enabled:
        raise HTTPException(status_code=409, detail="该租户已登记相同的服务器地址和 SSH 端口")
    if host is None:
        host = Host(
            tenant_id=user.tenant_id,
            address=body.address,
            ssh_port=body.ssh_port,
            ssh_user=body.ssh_user,
            ssh_password_encrypted=encrypt(body.ssh_password),
            os_family=facts["os_family"],
            os_version=facts["os_version"],
            architecture=facts["architecture"],
        )
        db.add(host)
    host.name = body.name
    host.ssh_user = body.ssh_user
    host.ssh_password_encrypted = encrypt(body.ssh_password)
    host.use_sudo = body.use_sudo
    host.sudo_password_encrypted = (
        encrypt(body.sudo_password or body.ssh_password) if body.use_sudo else None
    )
    host.os_family = facts["os_family"]
    host.os_version = facts["os_version"]
    host.architecture = facts["architecture"]
    host.facts = facts
    host.connection_status = "verified"
    host.last_tested_at = datetime.utcnow()
    host.enabled = True
    db.flush()
    audit(
        db,
        user,
        "restore" if restored else "create",
        "host",
        host.id,
        {"address": host.address, "ssh_user": host.ssh_user},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该租户已登记相同的服务器地址和 SSH 端口")
    return host_view(host)


@app.put("/api/hosts/{host_id}")
def update_host(
    host_id: str,
    body: HostCreate,
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)),
    db: Session = Depends(get_db),
):
    host = db.get(Host, host_id)
    if (
        not host
        or not host.enabled
        or (user.role != Role.SUPER_ADMIN and host.tenant_id != user.tenant_id)
    ):
        raise HTTPException(status_code=404, detail="未找到服务器资产")
    try:
        facts = probe_host(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"更新前连接测试失败：{str(exc)[-2000:]}")

    host.name = body.name
    host.address = body.address
    host.ssh_port = body.ssh_port
    host.ssh_user = body.ssh_user
    host.ssh_password_encrypted = encrypt(body.ssh_password)
    host.use_sudo = body.use_sudo
    host.sudo_password_encrypted = (
        encrypt(body.sudo_password or body.ssh_password) if body.use_sudo else None
    )
    host.os_family = facts["os_family"]
    host.os_version = facts["os_version"]
    host.architecture = facts["architecture"]
    host.facts = facts
    host.connection_status = "verified"
    host.last_tested_at = datetime.utcnow()
    audit(
        db,
        user,
        "update_credentials",
        "host",
        host.id,
        {"address": host.address, "ssh_user": host.ssh_user},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该租户已登记相同的服务器地址和 SSH 端口")
    db.refresh(host)
    return host_view(host)


@app.delete("/api/hosts/{host_id}", status_code=204)
def delete_host(
    host_id: str,
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)),
    db: Session = Depends(get_db),
):
    host = db.get(Host, host_id)
    if (
        not host
        or not host.enabled
        or (user.role != Role.SUPER_ADMIN and host.tenant_id != user.tenant_id)
    ):
        raise HTTPException(status_code=404, detail="未找到服务器资产")
    active_statuses = (
        TaskStatus.DRAFT,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.ROLLBACK_QUEUED,
        TaskStatus.ROLLING_BACK,
    )
    active_deployments = db.scalars(
        tenant_query(Deployment, user).where(
            Deployment.deleted_at.is_(None),
            Deployment.status.in_(active_statuses),
        )
    ).all()
    if any(
        instance.get("host_id") == host.id
        for deployment in active_deployments
        for instance in deployment.config.get("instances", [])
    ):
        raise HTTPException(status_code=409, detail="该服务器仍被未完成任务引用，不能删除")
    host.enabled = False
    host.connection_status = "removed"
    audit(db, user, "delete", "host", host.id, {"address": host.address})
    db.commit()


def host_view(host: Host) -> dict:
    return {
        "id": host.id,
        "name": host.name,
        "address": host.address,
        "ssh_port": host.ssh_port,
        "ssh_user": host.ssh_user,
        "use_sudo": host.use_sudo,
        "os_family": host.os_family,
        "os_version": host.os_version,
        "architecture": host.architecture,
        "facts": host.facts,
        "connection_status": host.connection_status,
        "last_tested_at": host.last_tested_at,
        "enabled": host.enabled,
    }


@app.get("/api/packages")
def list_packages(
    component: str | None = None,
    q: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    statement = select(Package).where(Package.enabled.is_(True))
    if component:
        statement = statement.where(Package.component == component.lower())
    if user.role != Role.SUPER_ADMIN:
        statement = statement.where(
            (Package.tenant_id == user.tenant_id) | (Package.tenant_id.is_(None))
        )
    if q and (term := q.strip()):
        pattern = f"%{term}%"
        statement = statement.where(
            or_(
                Package.component.ilike(pattern),
                Package.version.ilike(pattern),
                Package.filename.ilike(pattern),
                Package.description.ilike(pattern),
                Package.architecture.ilike(pattern),
            )
        )
    return [
        package_view(item)
        for item in db.scalars(statement.order_by(Package.created_at.desc())).all()
    ]


@app.post("/api/packages", status_code=201)
async def upload_package(
    component: str,
    version: str,
    package_type: PackageType,
    architecture: str = "x86_64",
    description: str = "",
    file: UploadFile = File(...),
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN)),
    db: Session = Depends(get_db),
):
    metadata = PackageMetadata(
        component=component,
        version=version,
        package_type=package_type,
        architecture=architecture,
        description=description,
    )
    if not file.filename or not file.filename.endswith((".tar.gz", ".tgz")):
        raise HTTPException(status_code=400, detail="仅支持上传 .tar.gz 或 .tgz 软件包")
    package_id = uuid4().hex
    target = Path(settings().packages_dir) / f"{package_id}.tar.gz"
    with target.open("wb") as output:
        while content := await file.read(1024 * 1024):
            output.write(content)
    package = Package(
        id=package_id,
        tenant_id=None if user.role == Role.SUPER_ADMIN else user.tenant_id,
        component=metadata.component,
        version=metadata.version,
        filename=file.filename,
        storage_path=str(target),
        package_type=metadata.package_type,
        architecture=metadata.architecture,
        description=metadata.description,
        uploaded_by=user.id,
    )
    db.add(package)
    db.flush()
    audit(
        db,
        user,
        "upload",
        "package",
        package.id,
        {
            "component": package.component,
            "version": package.version,
            "package_type": package.package_type.value,
        },
    )
    db.commit()
    return package_view(package)


@app.delete("/api/packages/{package_id}", status_code=204)
def delete_package(
    package_id: str,
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN)),
    db: Session = Depends(get_db),
):
    package = db.get(Package, package_id)
    if (
        not package
        or not package.enabled
        or (package.tenant_id is None and user.role != Role.SUPER_ADMIN)
        or (
            package.tenant_id is not None
            and user.role != Role.SUPER_ADMIN
            and package.tenant_id != user.tenant_id
        )
    ):
        raise HTTPException(status_code=404, detail="未找到软件包")
    active_statuses = (
        TaskStatus.DRAFT,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.ROLLBACK_QUEUED,
        TaskStatus.ROLLING_BACK,
    )
    active_reference = db.scalar(
        select(Deployment.id)
        .where(
            Deployment.package_id == package.id,
            Deployment.deleted_at.is_(None),
            Deployment.status.in_(active_statuses),
        )
        .limit(1)
    )
    if active_reference:
        raise HTTPException(status_code=409, detail="软件包仍被未完成任务引用，不能删除")

    package_root = Path(settings().packages_dir).resolve()
    package_path = Path(package.storage_path).resolve()
    if package_path.parent != package_root:
        raise HTTPException(status_code=409, detail="软件包存储路径不在平台受控目录，拒绝删除")
    try:
        package_path.unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"删除软件包文件失败：{exc}")
    package.enabled = False
    package.deleted_at = datetime.utcnow()
    audit(
        db,
        user,
        "delete",
        "package",
        package.id,
        {"filename": package.filename, "component": package.component},
    )
    db.commit()


def package_view(item: Package) -> dict:
    return {
        "id": item.id,
        "component": item.component,
        "version": item.version,
        "filename": item.filename,
        "package_type": item.package_type.value,
        "architecture": item.architecture,
        "description": item.description,
        "is_global": item.tenant_id is None,
        "created_at": item.created_at,
    }


def make_report_snapshot(
    deployment: Deployment,
    package: Package,
    config: dict,
    hosts_by_id: dict[str, Host],
) -> dict:
    return {
        "task_id": deployment.id,
        "task_name": deployment.name,
        "mode": deployment.mode.value,
        "created_at": (deployment.created_at or datetime.utcnow()).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "completed_at": "",
        "package": {
            "version": package.version,
            "package_type": package.package_type.value,
            "filename": package.filename,
            "architecture": package.architecture,
        },
        "config": {
            "primary_count": config["primary_count"],
            "replicas_per_primary": config["replicas_per_primary"],
            "appendonly": config["appendonly"],
            "maxmemory": config.get("maxmemory"),
            "maxmemory_policy": config["maxmemory_policy"],
        },
        "instances": [
            {
                "host_id": instance["host_id"],
                "host_name": hosts_by_id[instance["host_id"]].name,
                "address": hosts_by_id[instance["host_id"]].address,
                "ssh_port": hosts_by_id[instance["host_id"]].ssh_port,
                "redis_port": instance["port"],
                "bus_port": instance["port"] + 10000,
                "service_name": (
                    f"spmp-redis-{deployment.id[:8]}-redis_{index:03d}"
                ),
                "install_dir": instance["install_dir"],
                "data_dir": instance["data_dir"],
                "log_dir": instance["log_dir"],
                "config_dir": instance["config_dir"],
            }
            for index, instance in enumerate(config["instances"], 1)
        ],
    }


@app.post("/api/deployments", status_code=201)
def create_deployment(
    body: DeploymentCreate,
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)),
    db: Session = Depends(get_db),
):
    if not user.tenant_id:
        raise HTTPException(status_code=400, detail="当前用户未关联租户")
    package = db.get(Package, body.package_id)
    if (
        not package
        or not package.enabled
        or package.component != body.component
        or (package.tenant_id and package.tenant_id != user.tenant_id)
    ):
        raise HTTPException(status_code=404, detail="未找到可用的软件包")
    host_ids = {instance.host_id for instance in body.config.instances}
    hosts = db.scalars(
        select(Host).where(Host.id.in_(host_ids), Host.enabled.is_(True))
    ).all()
    if (
        len(hosts) != len(host_ids)
        or any(host.tenant_id != user.tenant_id for host in hosts)
        or any(host.connection_status != "verified" for host in hosts)
    ):
        raise HTTPException(status_code=400, detail="拓扑中包含无效、未验证或跨租户服务器")
    config = body.config.model_dump(mode="json")
    config["redis_password"] = encrypt(config["redis_password"])
    deployment = Deployment(
        tenant_id=user.tenant_id,
        name=body.name,
        component=body.component,
        mode=body.mode,
        package_id=package.id,
        status=TaskStatus.DRAFT,
        requested_by=user.id,
        config=config,
    )
    db.add(deployment)
    db.flush()
    hosts_by_id = {host.id: host for host in hosts}
    deployment.report_snapshot = make_report_snapshot(
        deployment,
        package,
        body.config.model_dump(mode="json"),
        hosts_by_id,
    )
    db.add(TaskLog(deployment_id=deployment.id, message="任务配置已保存，等待操作人员开始部署"))
    audit(
        db,
        user,
        "create",
        "deployment",
        deployment.id,
        {
            "component": body.component,
            "mode": body.mode.value,
            "instances": len(body.config.instances),
        },
    )
    db.commit()
    return deployment_view(deployment)


@app.get("/api/deployments")
def list_deployments(
    q: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    statement = tenant_query(Deployment, user).where(Deployment.deleted_at.is_(None))
    if q and (term := q.strip()):
        pattern = f"%{term}%"
        statement = statement.where(
            or_(
                Deployment.name.ilike(pattern),
                Deployment.id.ilike(pattern),
                Deployment.component.ilike(pattern),
                Deployment.status.ilike(pattern),
            )
        )
    return [
        deployment_view(item)
        for item in db.scalars(statement.order_by(Deployment.created_at.desc())).all()
    ]


@app.get("/api/deployments/{deployment_id}")
def get_deployment(
    deployment_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return deployment_view(owned_deployment(db, deployment_id, user))


@app.get("/api/deployments/{deployment_id}/logs")
def deployment_logs(
    deployment_id: str,
    after_id: int = 0,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    deployment = owned_deployment(db, deployment_id, user)
    logs = db.scalars(
        select(TaskLog)
        .where(TaskLog.deployment_id == deployment.id, TaskLog.id > after_id)
        .order_by(TaskLog.id)
    ).all()
    return [
        {"id": item.id, "created_at": item.created_at, "level": item.level, "message": item.message}
        for item in logs
    ]


@app.post("/api/deployments/{deployment_id}/start", status_code=202)
def start_deployment(
    deployment_id: str,
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)),
    db: Session = Depends(get_db),
):
    deployment = owned_deployment(db, deployment_id, user)
    if deployment.status != TaskStatus.DRAFT:
        raise HTTPException(status_code=409, detail="只有尚未开始的任务可以执行部署")
    deployment.status = TaskStatus.QUEUED
    db.add(TaskLog(deployment_id=deployment.id, message="操作人员已确认开始部署，等待 Worker 接收任务"))
    audit(db, user, "start", "deployment", deployment.id, {})
    db.commit()
    db.refresh(deployment)
    return deployment_view(deployment)


@app.post("/api/deployments/{deployment_id}/retry", status_code=202)
def retry_deployment(
    deployment_id: str,
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)),
    db: Session = Depends(get_db),
):
    old = owned_deployment(db, deployment_id, user)
    retryable_preflight_failure = (
        old.status == TaskStatus.FAILED
        and old.rollback_result is not None
        and "预检失败" in old.rollback_result
    )
    if old.status != TaskStatus.ROLLED_BACK and not retryable_preflight_failure:
        raise HTTPException(status_code=409, detail="只有失败且已回滚的任务可以重试")
    package = db.get(Package, old.package_id)
    if (
        not package
        or not package.enabled
        or not Path(package.storage_path).is_file()
    ):
        raise HTTPException(status_code=409, detail="原任务软件包已删除或文件不存在，不能创建重试任务")
    host_ids = {item["host_id"] for item in old.config["instances"]}
    hosts = db.scalars(
        select(Host).where(Host.id.in_(host_ids), Host.enabled.is_(True))
    ).all()
    if (
        len(hosts) != len(host_ids)
        or any(host.tenant_id != old.tenant_id for host in hosts)
        or any(host.connection_status != "verified" for host in hosts)
    ):
        raise HTTPException(status_code=409, detail="原任务包含已删除或未验证服务器，不能创建重试任务")
    retry = Deployment(
        tenant_id=old.tenant_id,
        name=f"{old.name}-retry",
        component=old.component,
        mode=old.mode,
        package_id=old.package_id,
        status=TaskStatus.DRAFT,
        requested_by=user.id,
        config=old.config,
    )
    db.add(retry)
    db.flush()
    hosts_by_id = {host.id: host for host in hosts}
    retry.report_snapshot = make_report_snapshot(
        retry,
        package,
        retry.config,
        hosts_by_id,
    )
    db.add(TaskLog(deployment_id=retry.id, message=f"重试任务已创建，等待手动开始；来源：{old.id}"))
    audit(db, user, "retry", "deployment", retry.id, {"source": old.id})
    db.commit()
    return deployment_view(retry)


@app.post("/api/deployments/{deployment_id}/rollback", status_code=202)
def retry_deployment_rollback(
    deployment_id: str,
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)),
    db: Session = Depends(get_db),
):
    deployment = owned_deployment(db, deployment_id, user)
    if deployment.status != TaskStatus.FAILED:
        raise HTTPException(status_code=409, detail="只有回滚不完整的失败任务可以重新回滚")
    deployment.status = TaskStatus.ROLLBACK_QUEUED
    db.add(
        TaskLog(
            deployment_id=deployment.id,
            message="操作人员请求重新回滚，等待 Worker 接收任务",
            level="WARN",
        )
    )
    audit(db, user, "retry_rollback", "deployment", deployment.id, {})
    db.commit()
    db.refresh(deployment)
    return deployment_view(deployment)


@app.delete("/api/deployments/{deployment_id}", status_code=204)
def delete_deployment(
    deployment_id: str,
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)),
    db: Session = Depends(get_db),
):
    deployment = owned_deployment(db, deployment_id, user)
    if deployment.deleted_at is not None:
        raise HTTPException(status_code=404, detail="未找到任务")
    if deployment.status not in (
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.ROLLED_BACK,
        TaskStatus.CANCELLED,
    ):
        raise HTTPException(status_code=409, detail="只有已完成、失败或已回滚任务可以删除")
    deployment.deleted_at = datetime.utcnow()
    audit(
        db,
        user,
        "delete",
        "deployment",
        deployment.id,
        {"status": deployment.status.value, "name": deployment.name},
    )
    db.commit()


@app.get("/api/deployments/{deployment_id}/report")
def download_deployment_report(
    deployment_id: str,
    user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)),
    db: Session = Depends(get_db),
):
    deployment = owned_deployment(db, deployment_id, user)
    if deployment.status != TaskStatus.SUCCEEDED:
        raise HTTPException(status_code=409, detail="只有部署成功的任务可以下载交付报告")
    snapshot = dict(deployment.report_snapshot or {})
    package = db.get(Package, deployment.package_id)
    if not package:
        raise HTTPException(status_code=409, detail="报告关联的软件包记录不存在")
    if not snapshot:
        host_ids = {item["host_id"] for item in deployment.config["instances"]}
        hosts = db.scalars(select(Host).where(Host.id.in_(host_ids))).all()
        hosts_by_id = {host.id: host for host in hosts}
        if len(hosts_by_id) != len(host_ids):
            raise HTTPException(status_code=409, detail="历史任务的服务器快照信息不完整")
        snapshot = make_report_snapshot(
            deployment,
            package,
            deployment.config,
            hosts_by_id,
        )
        deployment.report_snapshot = snapshot
    report_path = report_path_for(deployment.id)
    if not report_path.is_file():
        snapshot["completed_at"] = (
            deployment.updated_at.strftime("%Y-%m-%d %H:%M:%S")
        )
        build_report(
            snapshot,
            redis_password=decrypt(deployment.config["redis_password"]),
            output_path=report_path,
        )
        deployment.report_path = str(report_path)
        deployment.report_generated_at = datetime.utcnow()
    audit(
        db,
        user,
        "download",
        "deployment_report",
        deployment.id,
        {"filename": report_path.name},
    )
    db.commit()
    return FileResponse(
        report_path,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        filename=f"SPMP-Redis-{deployment.id}.docx",
    )


def owned_deployment(db: Session, deployment_id: str, user: User) -> Deployment:
    deployment = db.get(Deployment, deployment_id)
    if (
        not deployment
        or deployment.deleted_at is not None
        or (user.role != Role.SUPER_ADMIN and deployment.tenant_id != user.tenant_id)
    ):
        raise HTTPException(status_code=404, detail="未找到任务")
    return deployment


def deployment_view(item: Deployment) -> dict:
    safe_config = dict(item.config)
    safe_config.pop("redis_password", None)
    instances = []
    for instance in safe_config.get("instances", []):
        visible = dict(instance)
        visible.pop("custom_redis_conf", None)
        instances.append(visible)
    safe_config["instances"] = instances
    return {
        "id": item.id,
        "name": item.name,
        "component": item.component,
        "mode": item.mode.value,
        "status": item.status.value,
        "config": safe_config,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "rollback_result": item.rollback_result,
        "report_available": item.status == TaskStatus.SUCCEEDED,
    }


@app.get("/api/audit-logs")
def list_audit_logs(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    statement = select(AuditLog)
    if user.role != Role.SUPER_ADMIN:
        statement = statement.where(AuditLog.tenant_id == user.tenant_id)
    items = db.scalars(statement.order_by(AuditLog.created_at.desc()).limit(500)).all()
    return [
        {
            "id": item.id,
            "created_at": item.created_at,
            "action": item.action,
            "resource_type": item.resource_type,
            "resource_id": item.resource_id,
            "detail": item.detail,
        }
        for item in items
    ]


@app.post("/api/admin/tenants", status_code=201)
def create_tenant(
    body: TenantCreate,
    user: User = Depends(require(Role.SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    tenant = Tenant(name=body.name)
    db.add(tenant)
    db.flush()
    audit(db, user, "create", "tenant", tenant.id)
    db.commit()
    return {"id": tenant.id, "name": tenant.name}


@app.post("/api/admin/users", status_code=201)
def create_user(
    body: UserCreate,
    user: User = Depends(require(Role.SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    if body.role != Role.SUPER_ADMIN and not body.tenant_id:
        raise HTTPException(status_code=400, detail="非超级管理员必须指定租户")
    target = User(
        username=body.username,
        password_hash=password_hash(body.password),
        tenant_id=body.tenant_id,
        role=body.role,
    )
    db.add(target)
    db.flush()
    audit(db, user, "create", "user", target.id)
    db.commit()
    return user_view(target)


@app.put("/api/admin/ldap")
def save_ldap(
    body: LdapConfigRequest,
    user: User = Depends(require(Role.SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    config = db.scalar(select(LdapConfig))
    if not config:
        config = LdapConfig(
            server_url=body.server_url,
            base_dn=body.base_dn,
            bind_dn=body.bind_dn,
            bind_password_encrypted=encrypt(body.bind_password),
        )
        db.add(config)
        db.flush()
    else:
        config.server_url = body.server_url
        config.base_dn = body.base_dn
        config.bind_dn = body.bind_dn
        config.bind_password_encrypted = encrypt(body.bind_password)
    config.enabled = body.enabled
    config.user_filter = body.user_filter
    config.group_role_mapping = body.group_role_mapping
    audit(db, user, "update", "ldap_config", config.id)
    db.commit()
    return {
        "id": config.id,
        "enabled": config.enabled,
        "server_url": config.server_url,
        "base_dn": config.base_dn,
        "bind_dn": config.bind_dn,
        "user_filter": config.user_filter,
        "group_role_mapping": config.group_role_mapping,
    }


@app.get("/api/admin/ldap")
def get_ldap(
    user: User = Depends(require(Role.SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    config = db.scalar(select(LdapConfig))
    if not config:
        return {
            "enabled": False,
            "server_url": "",
            "base_dn": "",
            "bind_dn": "",
            "user_filter": "(uid={username})",
            "group_role_mapping": {},
        }
    return {
        "enabled": config.enabled,
        "server_url": config.server_url,
        "base_dn": config.base_dn,
        "bind_dn": config.bind_dn,
        "user_filter": config.user_filter,
        "group_role_mapping": config.group_role_mapping,
    }
