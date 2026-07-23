import hashlib
import os
import shutil
from pathlib import Path
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ldap3 import ALL, Connection, Server
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import settings
from app.database import Base, engine, get_db
from app.models import AuditLog, Deployment, Host, LdapConfig, Package, Role, TaskLog, TaskStatus, Tenant, User
from app.schemas import DeploymentCreate, HostCreate, LdapConfigRequest, LoginRequest, TenantCreate, UserCreate
from app.security import decrypt, encrypt, password_hash, password_matches, token_for, token_subject

app = FastAPI(title="SP MediDeploy Platform", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
bearer = HTTPBearer()


@app.on_event("startup")
def bootstrap() -> None:
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        if not db.scalar(select(User).where(User.username == settings().spmp_bootstrap_admin)):
            db.add(User(username=settings().spmp_bootstrap_admin, password_hash=password_hash(settings().spmp_bootstrap_password), role=Role.SUPER_ADMIN))
            db.commit()
    Path(settings().packages_dir).mkdir(parents=True, exist_ok=True)


def audit(db: Session, user: User | None, action: str, resource: str, resource_id: str | None, detail: dict | None = None) -> None:
    db.add(AuditLog(tenant_id=user.tenant_id if user else None, user_id=user.id if user else None, action=action, resource_type=resource, resource_id=resource_id, detail=detail or {}))


def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)) -> User:
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
def health():
    return {"status": "ok", "service": "SPMP"}


@app.post("/api/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user and user.auth_source == "local" and user.password_hash and password_matches(body.password, user.password_hash):
        audit(db, user, "login", "user", user.id); db.commit()
        return {"access_token": token_for(user.id), "user": user_view(user)}
    ldap = db.scalar(select(LdapConfig).where(LdapConfig.enabled.is_(True)))
    if not ldap:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    try:
        server = Server(ldap.server_url, get_info=ALL)
        service = Connection(server, user=ldap.bind_dn, password=decrypt(ldap.bind_password_encrypted), auto_bind=True)
        found = service.search(ldap.base_dn, ldap.user_filter.format(username=body.username), attributes=["memberOf"])
        if not found or not service.entries:
            raise ValueError("未找到 LDAP 用户")
        entry = service.entries[0]
        user_dn = entry.entry_dn
        if not Connection(server, user=user_dn, password=body.password, auto_bind=True).bound:
            raise ValueError("LDAP 密码错误")
        groups = [str(x) for x in getattr(entry, "memberOf", [])]
        role = next((Role(v) for g, v in ldap.group_role_mapping.items() if g in groups and v in Role._value2member_map_), Role.OPERATOR)
        if not user:
            user = User(username=body.username, tenant_id=None, password_hash=None, role=role, auth_source="ldap")
            db.add(user); db.flush()
        audit(db, user, "ldap_login", "user", user.id); db.commit()
        return {"access_token": token_for(user.id), "user": user_view(user)}
    except Exception:
        raise HTTPException(status_code=401, detail="LDAP 认证失败")


def user_view(user: User) -> dict:
    return {"id": user.id, "username": user.username, "tenant_id": user.tenant_id, "role": user.role.value, "auth_source": user.auth_source}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return user_view(user)


@app.get("/api/hosts")
def list_hosts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [host_view(x) for x in db.scalars(tenant_query(Host, user).order_by(Host.created_at.desc())).all()]


@app.post("/api/hosts", status_code=201)
def create_host(body: HostCreate, user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)), db: Session = Depends(get_db)):
    if not user.tenant_id and user.role != Role.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="普通用户必须属于一个租户")
    tenant_id = user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="超级管理员请先创建租户并使用租户管理员账号操作")
    host = Host(tenant_id=tenant_id, name=body.name, address=body.address, ssh_port=body.ssh_port, ssh_user=body.ssh_user, ssh_private_key_encrypted=encrypt(body.ssh_private_key), os_family=body.os_family, os_version=body.os_version, architecture=body.architecture)
    db.add(host); db.flush(); audit(db, user, "create", "host", host.id, {"address": host.address}); db.commit()
    return host_view(host)


def host_view(host: Host) -> dict:
    return {"id": host.id, "name": host.name, "address": host.address, "ssh_port": host.ssh_port, "ssh_user": host.ssh_user, "os_family": host.os_family, "os_version": host.os_version, "architecture": host.architecture, "enabled": host.enabled}


@app.get("/api/packages")
def list_packages(user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Package).where(Package.component == "redis")
    if user.role != Role.SUPER_ADMIN:
        stmt = stmt.where((Package.tenant_id == user.tenant_id) | (Package.tenant_id.is_(None)))
    return [package_view(x) for x in db.scalars(stmt.order_by(Package.created_at.desc())).all()]


@app.post("/api/packages/redis", status_code=201)
async def upload_redis_package(version: str, file: UploadFile = File(...), user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN)), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.endswith((".tar.gz", ".tgz")):
        raise HTTPException(status_code=400, detail="仅允许上传 Redis 源码 .tar.gz/.tgz 包")
    package_id = __import__("uuid").uuid4().hex
    target = Path(settings().packages_dir) / f"{package_id}.tar.gz"
    digest = hashlib.sha256()
    with target.open("wb") as output:
        while content := await file.read(1024 * 1024):
            digest.update(content); output.write(content)
    package = Package(id=package_id, tenant_id=None if user.role == Role.SUPER_ADMIN else user.tenant_id, version=version, filename=file.filename, storage_path=str(target), sha256=digest.hexdigest(), uploaded_by=user.id)
    db.add(package); db.flush(); audit(db, user, "upload", "package", package.id, {"version": version, "sha256": package.sha256}); db.commit()
    return package_view(package)


def package_view(item: Package) -> dict:
    return {"id": item.id, "component": item.component, "version": item.version, "filename": item.filename, "sha256": item.sha256, "package_type": item.package_type, "architecture": item.architecture, "created_at": item.created_at}


@app.post("/api/deployments/redis", status_code=202)
def create_redis_deployment(body: DeploymentCreate, user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)), db: Session = Depends(get_db)):
    try:
        body.config.validate_cluster_shape()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    package = db.get(Package, body.package_id)
    if not package or package.component != "redis" or (package.tenant_id and package.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="未找到可用 Redis 软件包")
    hosts = db.scalars(select(Host).where(Host.id.in_(body.config.node_ids), Host.enabled.is_(True))).all()
    if len(hosts) != len(body.config.node_ids) or any(h.tenant_id != user.tenant_id for h in hosts):
        raise HTTPException(status_code=400, detail="包含无效、停用或跨租户主机")
    if any(h.architecture != "x86_64" for h in hosts):
        raise HTTPException(status_code=400, detail="当前 Redis 包仅支持 x86_64 主机")
    config = body.config.model_dump()
    # 密码绝不以明文写入任务表；Worker 在启动 Ansible 前临时解密。
    config["redis_password"] = encrypt(config["redis_password"])
    deployment = Deployment(tenant_id=user.tenant_id, name=body.name, package_id=package.id, requested_by=user.id, config=config)
    db.add(deployment); db.flush(); db.add(TaskLog(deployment_id=deployment.id, message="任务已提交，等待 Worker 执行预检")); audit(db, user, "create", "redis_deployment", deployment.id, {"name": body.name, "nodes": body.config.node_ids}); db.commit()
    return deployment_view(deployment)


@app.get("/api/deployments")
def list_deployments(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [deployment_view(x) for x in db.scalars(tenant_query(Deployment, user).order_by(Deployment.created_at.desc())).all()]


@app.get("/api/deployments/{deployment_id}/logs")
def deployment_logs(deployment_id: str, after_id: int = 0, user: User = Depends(current_user), db: Session = Depends(get_db)):
    deployment = owned_deployment(db, deployment_id, user)
    logs = db.scalars(select(TaskLog).where(TaskLog.deployment_id == deployment.id, TaskLog.id > after_id).order_by(TaskLog.id)).all()
    return [{"id": x.id, "created_at": x.created_at, "level": x.level, "message": x.message} for x in logs]


@app.post("/api/deployments/{deployment_id}/retry", status_code=202)
def retry_deployment(deployment_id: str, user: User = Depends(require(Role.SUPER_ADMIN, Role.TENANT_ADMIN, Role.OPERATOR)), db: Session = Depends(get_db)):
    old = owned_deployment(db, deployment_id, user)
    if old.status not in (TaskStatus.FAILED, TaskStatus.ROLLED_BACK):
        raise HTTPException(status_code=409, detail="只有失败且已回滚的任务可重试")
    retry = Deployment(tenant_id=old.tenant_id, name=f"{old.name}-retry", package_id=old.package_id, requested_by=user.id, config=old.config)
    db.add(retry); db.flush(); db.add(TaskLog(deployment_id=retry.id, message=f"重试任务，来源：{old.id}")); audit(db, user, "retry", "redis_deployment", retry.id, {"source": old.id}); db.commit()
    return deployment_view(retry)


def owned_deployment(db: Session, deployment_id: str, user: User) -> Deployment:
    deployment = db.get(Deployment, deployment_id)
    if not deployment or (user.role != Role.SUPER_ADMIN and deployment.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="未找到任务")
    return deployment


def deployment_view(item: Deployment) -> dict:
    safe_config = dict(item.config)
    safe_config.pop("redis_password", None)
    safe_config.pop("custom_redis_conf", None)
    return {"id": item.id, "name": item.name, "component": item.component, "status": item.status.value, "config": safe_config, "created_at": item.created_at, "updated_at": item.updated_at, "rollback_result": item.rollback_result}


@app.post("/api/admin/tenants", status_code=201)
def create_tenant(body: TenantCreate, user: User = Depends(require(Role.SUPER_ADMIN)), db: Session = Depends(get_db)):
    tenant = Tenant(name=body.name); db.add(tenant); db.flush(); audit(db, user, "create", "tenant", tenant.id); db.commit(); return {"id": tenant.id, "name": tenant.name}


@app.post("/api/admin/users", status_code=201)
def create_user(body: UserCreate, user: User = Depends(require(Role.SUPER_ADMIN)), db: Session = Depends(get_db)):
    if body.role != Role.SUPER_ADMIN and not body.tenant_id:
        raise HTTPException(status_code=400, detail="非超级管理员必须指定租户")
    target = User(username=body.username, password_hash=password_hash(body.password), tenant_id=body.tenant_id, role=body.role)
    db.add(target); db.flush(); audit(db, user, "create", "user", target.id); db.commit(); return user_view(target)


@app.put("/api/admin/ldap")
def save_ldap(body: LdapConfigRequest, user: User = Depends(require(Role.SUPER_ADMIN)), db: Session = Depends(get_db)):
    config = db.scalar(select(LdapConfig))
    if not config:
        config = LdapConfig(server_url=body.server_url, base_dn=body.base_dn, bind_dn=body.bind_dn, bind_password_encrypted=encrypt(body.bind_password)); db.add(config)
    else:
        config.server_url, config.base_dn, config.bind_dn = body.server_url, body.base_dn, body.bind_dn
        config.bind_password_encrypted = encrypt(body.bind_password)
    config.enabled, config.user_filter, config.group_role_mapping = body.enabled, body.user_filter, body.group_role_mapping
    audit(db, user, "update", "ldap_config", config.id); db.commit(); return {"id": config.id, "enabled": config.enabled, "server_url": config.server_url, "base_dn": config.base_dn, "bind_dn": config.bind_dn, "user_filter": config.user_filter, "group_role_mapping": config.group_role_mapping}


@app.get("/api/admin/ldap")
def get_ldap(user: User = Depends(require(Role.SUPER_ADMIN)), db: Session = Depends(get_db)):
    config = db.scalar(select(LdapConfig))
    if not config:
        return {"enabled": False, "server_url": "", "base_dn": "", "bind_dn": "", "user_filter": "(uid={username})", "group_role_mapping": {}}
    return {"enabled": config.enabled, "server_url": config.server_url, "base_dn": config.base_dn, "bind_dn": config.bind_dn, "user_filter": config.user_filter, "group_role_mapping": config.group_role_mapping}
