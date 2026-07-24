"""MySQL-backed deployment worker; no external message broker is required."""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.host_probe import connection_inventory_vars
from app.models import Deployment, Host, Package, TaskLog, TaskStatus
from app.security import decrypt


def add_log(deployment_id: str, message: str, level: str = "INFO") -> None:
    with SessionLocal() as db:
        db.add(TaskLog(deployment_id=deployment_id, message=message[-8000:], level=level))
        db.commit()


def claim_task() -> str | None:
    with SessionLocal.begin() as db:
        task = db.scalar(
            select(Deployment)
            .where(Deployment.status == TaskStatus.QUEUED)
            .order_by(Deployment.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not task:
            return None
        task.status = TaskStatus.RUNNING
        return task.id


def run_task(task_id: str) -> None:
    with SessionLocal() as db:
        task = db.get(Deployment, task_id)
        if not task:
            return
        package = db.get(Package, task.package_id)
        config = dict(task.config)
        host_ids = {item["host_id"] for item in config["instances"]}
        hosts = db.scalars(select(Host).where(Host.id.in_(host_ids))).all()
        hosts_by_id = {host.id: host for host in hosts}
        if not package or not Path(package.storage_path).is_file():
            fail(db, task, "软件包不存在，任务未对目标服务器做任何修改")
            return
        if len(hosts_by_id) != len(host_ids):
            fail(db, task, "目标服务器记录不完整，任务未执行")
            return
        if any(host.architecture != "x86_64" for host in hosts):
            fail(db, task, "目标服务器包含非 x86_64 架构，当前版本不支持")
            return
        db.commit()

    add_log(task_id, "开始预检：SSH 密码、sudo、Python 3、端口、目录、编译依赖和 systemd")
    try:
        deploy_result, rollback_result = call_ansible(
            task_id,
            package,
            config,
            hosts_by_id,
        )
    except Exception as exc:
        add_log(task_id, f"Worker 异常：{exc}", "ERROR")
        deploy_result, rollback_result = 1, 1

    with SessionLocal() as db:
        task = db.get(Deployment, task_id)
        if deploy_result == 0:
            task.status = TaskStatus.SUCCEEDED
            task.rollback_result = None
            db.add(
                TaskLog(
                    deployment_id=task_id,
                    message=(
                        "部署成功：Redis 普通实例已启动"
                        if task.mode.value == "standalone"
                        else "部署成功：Redis Cluster 已创建"
                    ),
                )
            )
        elif rollback_result is None:
            task.status = TaskStatus.FAILED
            task.rollback_result = "预检失败，未开始变更"
            db.add(
                TaskLog(
                    deployment_id=task_id,
                    message="预检失败，未对任何目标服务器执行安装或配置变更",
                    level="ERROR",
                )
            )
        elif rollback_result == 0:
            task.status = TaskStatus.ROLLED_BACK
            task.rollback_result = "部署失败，已按任务资源清单完成自动回滚"
            db.add(
                TaskLog(
                    deployment_id=task_id,
                    message="部署失败，已清理本任务创建的服务、用户和精确目录",
                    level="ERROR",
                )
            )
        else:
            task.status = TaskStatus.FAILED
            task.rollback_result = "部署失败且自动回滚未完全成功，请根据日志人工核验"
            db.add(
                TaskLog(
                    deployment_id=task_id,
                    message="部署和自动回滚均出现异常，请人工核验任务资源清单",
                    level="ERROR",
                )
            )
        db.commit()


def fail(db, task: Deployment, message: str) -> None:
    task.status = TaskStatus.FAILED
    task.rollback_result = "预检失败，未开始变更"
    db.add(TaskLog(deployment_id=task.id, message=message, level="ERROR"))
    db.commit()


def call_ansible(
    task_id: str,
    package: Package,
    config: dict,
    hosts_by_id: dict[str, Host],
) -> tuple[int, int | None]:
    with tempfile.TemporaryDirectory(prefix="spmp-task-") as temp_dir:
        root = Path(temp_dir)
        inventory_hosts = {}
        redis_nodes = []
        for index, instance in enumerate(config["instances"], start=1):
            host = hosts_by_id[instance["host_id"]]
            inventory_name = f"redis_{index:03d}"
            connection = connection_inventory_vars(
                address=host.address,
                port=host.ssh_port,
                user=host.ssh_user,
                password=decrypt(host.ssh_password_encrypted),
                use_sudo=host.use_sudo,
                sudo_password=decrypt(host.sudo_password_encrypted)
                if host.sudo_password_encrypted
                else None,
            )
            connection.update(
                {
                    "redis_instance_id": inventory_name,
                    "redis_advertise_address": host.address,
                    "redis_port": instance["port"],
                    "redis_install_path": instance["install_dir"],
                    "redis_data_path": instance["data_dir"],
                    "redis_log_path": instance["log_dir"],
                    "redis_config_path": instance["config_dir"],
                    "custom_redis_conf": instance.get("custom_redis_conf") or "",
                }
            )
            inventory_hosts[inventory_name] = connection
            redis_nodes.append(
                {
                    "address": host.address,
                    "port": instance["port"],
                    "bus_port": instance["port"] + 10000,
                }
            )

        inventory = {"all": {"children": {"redis": {"hosts": inventory_hosts}}}}
        inventory_path = root / "inventory.json"
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        os.chmod(inventory_path, 0o600)

        extra_vars = {
            "spmp_task_id": task_id,
            "redis_package_path": package.storage_path,
            "redis_package_type": package.package_type.value,
            "redis_deployment_mode": config["mode"],
            "redis_primary_count": config["primary_count"],
            "redis_replicas_per_primary": config["replicas_per_primary"],
            "redis_nodes": redis_nodes,
            "redis_password": decrypt(config["redis_password"]),
            "maxmemory": config.get("maxmemory"),
            "maxmemory_policy": config["maxmemory_policy"],
            "appendonly": config["appendonly"],
        }
        vars_path = root / "vars.json"
        vars_path.write_text(json.dumps(extra_vars), encoding="utf-8")
        os.chmod(vars_path, 0o600)

        process_env = os.environ.copy()
        process_env["ANSIBLE_ROLES_PATH"] = str(Path(settings().ansible_dir) / "roles")
        preflight_result = stream_playbook(
            task_id,
            inventory_path,
            vars_path,
            Path(settings().ansible_dir) / "playbooks" / "redis_preflight.yml",
            process_env,
        )
        if preflight_result != 0:
            add_log(
                task_id,
                "预检未通过，平台确认尚未开始任何安装或配置变更，因此不执行回滚",
                "ERROR",
            )
            return preflight_result, None

        add_log(task_id, "全部实例预检通过，开始分发、安装和启动 Redis")
        deploy_result = stream_playbook(
            task_id,
            inventory_path,
            vars_path,
            Path(settings().ansible_dir) / "playbooks" / "redis.yml",
            process_env,
        )
        if deploy_result == 0:
            return 0, 0
        with SessionLocal() as db:
            failed_task = db.get(Deployment, task_id)
            if failed_task:
                failed_task.status = TaskStatus.ROLLING_BACK
                db.commit()
        add_log(task_id, "开始执行本任务精确资源清单回滚", "WARN")
        rollback_result = stream_playbook(
            task_id,
            inventory_path,
            vars_path,
            Path(settings().ansible_dir) / "playbooks" / "redis_rollback.yml",
            process_env,
        )
        return deploy_result, rollback_result


def stream_playbook(
    task_id: str,
    inventory_path: Path,
    vars_path: Path,
    playbook_path: Path,
    process_env: dict,
) -> int:
    command = [
        "ansible-playbook",
        "-i",
        str(inventory_path),
        str(playbook_path),
        "--extra-vars",
        f"@{vars_path}",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=process_env,
    )
    assert process.stdout is not None
    for line in process.stdout:
        message = line.rstrip()
        level = "INFO"
        if "fatal:" in message or "FAILED!" in message:
            level = "ERROR"
        elif "[WARNING]" in message:
            level = "WARN"
        add_log(task_id, message, level)
    return process.wait()


def main() -> None:
    while True:
        try:
            task_id = claim_task()
            if task_id:
                run_task(task_id)
            else:
                time.sleep(settings().task_poll_seconds)
        except Exception as exc:
            print(f"worker loop error: {exc}", flush=True)
            time.sleep(settings().task_poll_seconds)


if __name__ == "__main__":
    main()
