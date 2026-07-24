from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ldap3 import ALL, Connection, Server
from sqlalchemy import select
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
def list_hosts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        host_view(item)
        for item in db.scalars(tenant_query(Host, user).order_by(Host.created_at.desc())).all()
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
    host = Host(
        tenant_id=user.tenant_id,
        name=body.name,
        address=body.address,
        ssh_port=body.ssh_port,
        ssh_user=body.ssh_user,
        ssh_password_encrypted=encrypt(body.ssh_password),
        use_sudo=body.use_sudo,
        sudo_password_encrypted=encrypt(body.sudo_password or body.ssh_password)
        if body.use_sudo
        else None,
        os_family=facts["os_family"],
        os_version=facts["os_version"],
        architecture=facts["architecture"],
        facts=facts,
        connection_status="verified",
        last_tested_at=datetime.utcnow(),
    )
    db.add(host)
    db.flush()
    audit(db, user, "create", "host", host.id, {"address": host.address, "ssh_user": host.ssh_user})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该租户已登记相同的服务器地址和 SSH 端口")
    return host_view(host)


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
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    statement = select(Package)
    if component:
        statement = statement.where(Package.component == component.lower())
    if user.role != Role.SUPER_ADMIN:
        statement = statement.where(
            (Package.tenant_id == user.tenant_id) | (Package.tenant_id.is_(None))
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


def package_view(item: Package) -> dict:
    return {
        "id": item.id,
        "component": item.component,
        "version": item.version,
        "filename": item.filename,
        "package_type": item.package_type.value,
        "architecture": item.architecture,
        "description": item.description,
        "created_at": item.created_at,
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
def list_deployments(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        deployment_view(item)
        for item in db.scalars(
            tenant_query(Deployment, user).order_by(Deployment.created_at.desc())
        ).all()
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
    if old.status not in (TaskStatus.FAILED, TaskStatus.ROLLED_BACK):
        raise HTTPException(status_code=409, detail="只有失败且已回滚的任务可以重试")
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
    db.add(TaskLog(deployment_id=retry.id, message=f"重试任务已创建，等待手动开始；来源：{old.id}"))
    audit(db, user, "retry", "deployment", retry.id, {"source": old.id})
    db.commit()
    return deployment_view(retry)


def owned_deployment(db: Session, deployment_id: str, user: User) -> Deployment:
    deployment = db.get(Deployment, deployment_id)
    if not deployment or (
        user.role != Role.SUPER_ADMIN and deployment.tenant_id != user.tenant_id
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
