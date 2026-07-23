"""MySQL-backed lightweight deployment worker; no external message broker is required."""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from sqlalchemy import select
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import Deployment, Host, Package, TaskLog, TaskStatus
from app.security import decrypt


def add_log(deployment_id: str, message: str, level: str = "INFO") -> None:
    with SessionLocal() as db:
        db.add(TaskLog(deployment_id=deployment_id, message=message[-8000:], level=level))
        db.commit()


def claim_task() -> str | None:
    with SessionLocal.begin() as db:
        task = db.scalar(select(Deployment).where(Deployment.status == TaskStatus.QUEUED).order_by(Deployment.created_at).with_for_update(skip_locked=True).limit(1))
        if not task:
            return None
        task.status = TaskStatus.RUNNING
        task_id = task.id
    return task_id


def run_task(task_id: str) -> None:
    with SessionLocal() as db:
        task = db.get(Deployment, task_id)
        package = db.get(Package, task.package_id)
        config = dict(task.config)
        hosts = db.scalars(select(Host).where(Host.id.in_(config["node_ids"]))).all()
        if not package or not Path(package.storage_path).is_file():
            fail(db, task, "软件包不存在，任务未对目标服务器做任何修改")
            return
        if len(hosts) != len(config["node_ids"]):
            fail(db, task, "目标服务器记录不完整，任务未执行")
            return
        order = {node_id: index for index, node_id in enumerate(config["node_ids"])}
        hosts.sort(key=lambda h: order[h.id])
        for host in hosts:
            if host.architecture != "x86_64":
                fail(db, task, f"主机 {host.name} 不是 x86_64，当前包不支持")
                return
        db.commit()
    add_log(task_id, "开始执行预检：检查 SSH、sudo、python3、tar、make、gcc 和 systemd。")
    try:
        result = call_ansible(task_id, package.storage_path, config, hosts)
    except Exception as exc:
        add_log(task_id, f"Worker 异常：{exc}", "ERROR")
        result = 1
    with SessionLocal() as db:
        task = db.get(Deployment, task_id)
        if result == 0:
            task.status = TaskStatus.SUCCEEDED
            add_log(task_id, "部署成功：Redis Cluster 已创建。")
        else:
            task.status = TaskStatus.ROLLED_BACK
            task.rollback_result = "Ansible 任务失败，已触发本次任务精确资源清单的自动回滚；请查看日志确认预检或目标环境问题。"
            add_log(task_id, "部署失败，Ansible 已执行自动回滚；未修改部署前已有资源。", "ERROR")
        db.commit()


def fail(db, task: Deployment, message: str) -> None:
    task.status = TaskStatus.FAILED
    task.rollback_result = "预检失败，未开始变更。"
    db.add(TaskLog(deployment_id=task.id, message=message, level="ERROR"))
    db.commit()


def call_ansible(task_id: str, package_path: str, config: dict, hosts: list[Host]) -> int:
    with tempfile.TemporaryDirectory(prefix="spmp-") as temp_dir:
        root = Path(temp_dir)
        inventory = {"all": {"children": {"redis": {"hosts": {}}}}}
        for host in hosts:
            key_path = root / f"key-{host.id}"
            key_path.write_text(decrypt(host.ssh_private_key_encrypted), encoding="utf-8")
            os.chmod(key_path, 0o600)
            inventory["all"]["children"]["redis"]["hosts"][host.name] = {
                "ansible_host": host.address, "ansible_port": host.ssh_port, "ansible_user": host.ssh_user,
                "ansible_ssh_private_key_file": str(key_path), "ansible_become": True, "ansible_become_method": "sudo"
            }
        (root / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
        config["redis_password"] = decrypt(config["redis_password"])
        extra_vars = {
            "spmp_task_id": task_id, "redis_package_path": package_path,
            "redis_nodes": [{"name": h.name, "address": h.address} for h in hosts],
            **config,
        }
        vars_file = root / "vars.json"
        vars_file.write_text(json.dumps(extra_vars), encoding="utf-8")
        command = ["ansible-playbook", "-i", str(root / "inventory.json"), str(Path(settings().ansible_dir) / "playbooks" / "redis_cluster.yml"), "--extra-vars", f"@{vars_file}"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert process.stdout is not None
        for line in process.stdout:
            add_log(task_id, line.rstrip())
        return process.wait()


def main() -> None:
    Base.metadata.create_all(bind=engine)
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
