import json
import os
import subprocess
import tempfile
from pathlib import Path

from app.config import settings
from app.schemas import HostConnection


FACT_PREFIX = "SPMP_FACTS|"
FACT_COMMAND = (
    "python3 -c 'import platform,shutil;"
    "d=dict(line.rstrip().split(\"=\",1) for line in open(\"/etc/os-release\") if \"=\" in line);"
    "u=shutil.disk_usage(\"/\");"
    "print(\"SPMP_FACTS|%s|%s|%s|%s\" % "
    "(d.get(\"ID\",\"unknown\").strip(chr(34)),"
    "d.get(\"VERSION_ID\",platform.release()).strip(chr(34)),"
    "platform.machine(),u.free))'"
)


def persistent_known_hosts_path() -> Path:
    path = Path(settings().packages_dir) / ".ssh_known_hosts"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    return path


def connection_inventory_vars(
    *,
    address: str,
    port: int,
    user: str,
    password: str,
    use_sudo: bool,
    sudo_password: str | None,
) -> dict:
    known_hosts = persistent_known_hosts_path()
    values = {
        "ansible_host": address,
        "ansible_port": port,
        "ansible_user": user,
        "ansible_password": password,
        "ansible_connection": "ssh",
        "ansible_ssh_common_args": (
            "-o StrictHostKeyChecking=accept-new "
            f"-o UserKnownHostsFile={known_hosts}"
        ),
        "ansible_become": use_sudo,
        "ansible_become_method": "sudo",
    }
    if use_sudo:
        values["ansible_become_password"] = sudo_password or password
    return values


def probe_host(connection: HostConnection) -> dict:
    with tempfile.TemporaryDirectory(prefix="spmp-probe-") as temp_dir:
        root = Path(temp_dir)
        inventory = {
            "all": {
                "hosts": {
                    "probe_target": connection_inventory_vars(
                        address=connection.address,
                        port=connection.ssh_port,
                        user=connection.ssh_user,
                        password=connection.ssh_password,
                        use_sudo=connection.use_sudo,
                        sudo_password=connection.sudo_password,
                    )
                }
            }
        }
        inventory_path = root / "inventory.json"
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        os.chmod(inventory_path, 0o600)
        command = [
            "ansible",
            "all",
            "-i",
            str(inventory_path),
            "-m",
            "ansible.builtin.raw",
            "-a",
            FACT_COMMAND,
        ]
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=40,
            env=os.environ.copy(),
            check=False,
        )
        if process.returncode != 0:
            detail = process.stdout.strip()[-3000:]
            raise RuntimeError(detail or "SSH、sudo 或 Python 3 预检失败")
        fact_line = next(
            (line.strip() for line in process.stdout.splitlines() if line.strip().startswith(FACT_PREFIX)),
            None,
        )
        if not fact_line:
            raise RuntimeError("已连接服务器，但未能读取操作系统信息，请确认目标机存在 Python 3")
        _, os_family, os_version, architecture, disk_free = fact_line.split("|", 4)
        if architecture not in {"x86_64", "amd64"}:
            raise RuntimeError(f"当前仅支持 x86_64，目标服务器架构为 {architecture}")
        return {
            "os_family": os_family,
            "os_version": os_version,
            "architecture": "x86_64",
            "disk_free_bytes": int(disk_free),
            "python3": True,
            "sudo": connection.use_sudo,
        }
