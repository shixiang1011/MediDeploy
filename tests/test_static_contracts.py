import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticContractTests(unittest.TestCase):
    def test_nginx_preserves_api_prefix_and_uses_docker_dns(self):
        config = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
        self.assertIn("location /api/", config)
        self.assertIn("resolver 127.0.0.11", config)
        self.assertIn("set $api_upstream http://api:8000;", config)
        self.assertIn("proxy_pass $api_upstream;", config)
        self.assertNotIn("proxy_pass http://api:8000/;", config)
        self.assertIn("client_max_body_size 5g;", config)
        self.assertIn("proxy_request_buffering off;", config)
        self.assertIn("proxy_read_timeout 3600s;", config)
        self.assertIn("proxy_send_timeout 3600s;", config)

    def test_frontend_routes_match_backend(self):
        frontend = (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
        backend = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        for frontend_path, backend_route in (
            ("'auth/login'", '@app.post("/api/auth/login")'),
            ("'hosts/test'", '@app.post("/api/hosts/test")'),
            ("'packages'", '@app.post("/api/packages"'),
            ("'deployments'", '@app.post("/api/deployments"'),
        ):
            self.assertIn(frontend_path, frontend)
            self.assertIn(backend_route, backend)
        self.assertIn('@app.get("/api/health")', backend)
        self.assertIn('@app.post("/api/deployments/{deployment_id}/start"', backend)

    def test_frontend_is_extensible_simplified_chinese_wizard(self):
        frontend = (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
        for label in (
            "软件包仓库",
            "部署中心",
            "选择中间件",
            "基础信息",
            "拓扑配置",
            "运行参数",
            "确认提交",
            "单机模式",
            "Redis Cluster",
        ):
            self.assertIn(label, frontend)
        self.assertNotIn("平台管理", frontend)

    def test_server_assets_use_password_not_private_key(self):
        models = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
        schemas = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
        frontend = (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
        self.assertIn("ssh_password_encrypted", models)
        self.assertIn("sudo_password_encrypted", models)
        self.assertIn("ssh_password", schemas)
        self.assertIn("SSH 登录密码", frontend)
        self.assertNotIn("ssh_private_key", models + schemas + frontend)

    def test_package_repository_supports_source_and_binary_tarballs(self):
        models = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
        api = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('SOURCE = "source"', models)
        self.assertIn('BINARY = "binary"', models)
        self.assertIn('(".tar.gz", ".tgz")', api)
        self.assertNotIn("sha256", models + api.lower())

    def test_redis_playbook_preflights_before_install_and_supports_binary(self):
        playbook = (ROOT / "ansible" / "playbooks" / "redis.yml").read_text(encoding="utf-8")
        preflight = (
            ROOT / "ansible" / "playbooks" / "redis_preflight.yml"
        ).read_text(encoding="utf-8")
        tasks = (ROOT / "ansible" / "roles" / "redis" / "tasks" / "main.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Preflight every Redis instance", preflight)
        self.assertIn("Wait for SSH and Python to become reachable", preflight)
        self.assertNotIn("Install and start every Redis instance", preflight)
        self.assertNotIn("Preflight every Redis instance", playbook)
        self.assertIn("Install and start every Redis instance", playbook)
        self.assertIn("redis_package_type == 'source'", tasks)
        self.assertIn("redis_package_type == 'binary'", tasks)
        self.assertIn('- "{{ redis_install_path }}/bin"', tasks)
        self.assertIn("Wait for Redis on the target loopback interface", tasks)
        self.assertIn('host: "127.0.0.1"', tasks)
        self.assertIn("REDISCLI_AUTH", tasks)
        self.assertIn("Collect Redis application log after a verification failure", tasks)
        self.assertNotIn('host: "{{ redis_advertise_address }}"', tasks)
        self.assertIn("setfacl", preflight)
        self.assertIn("Grant only this Redis service user traversal", tasks)
        self.assertIn("Verify every Redis client port", playbook)
        self.assertIn("Verify every Redis Cluster Bus port", playbook)
        self.assertIn("Verify Redis Cluster reaches the healthy state", playbook)

        rollback = (
            ROOT / "ansible" / "playbooks" / "redis_rollback.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("Revoke this task service user's parent traversal ACLs", rollback)
        self.assertIn("- -x", rollback)
        self.assertNotIn("serial: 1", rollback)

        service = (
            ROOT / "ansible" / "roles" / "redis" / "templates" / "redis.service.j2"
        ).read_text(encoding="utf-8")
        redis_config = (
            ROOT / "ansible" / "roles" / "redis" / "templates" / "redis.conf.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("Type=simple", service)
        self.assertNotIn("Type=notify", service)
        self.assertNotIn("--supervised systemd", service)
        self.assertIn("supervised no", redis_config)
        self.assertIn(
            "{% if maxmemory %}\nmaxmemory {{ maxmemory }}\n{% endif %}\n"
            "maxmemory-policy {{ maxmemory_policy }}",
            redis_config,
        )

    def test_deployments_require_manual_start_and_logs_auto_poll(self):
        models = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
        api = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        frontend = (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
        migration = (
            ROOT
            / "backend"
            / "migrations"
            / "versions"
            / "20260724_0002_deployment_draft_status.py"
        ).read_text(encoding="utf-8")

        self.assertIn('DRAFT = "draft"', models)
        self.assertIn("status=TaskStatus.DRAFT", api)
        self.assertIn("deployment.status = TaskStatus.QUEUED", api)
        self.assertIn('"DRAFT"', migration)
        self.assertIn("开始部署", frontend)
        self.assertIn("window.setInterval(() => this.pollExecution(), 1000)", frontend)
        self.assertIn("after_id=${this.executionLastLogId}", frontend)
        self.assertNotIn("showTaskLogs", frontend)

    def test_worker_runs_explicit_rollback_playbook(self):
        worker = (ROOT / "backend" / "app" / "worker.py").read_text(encoding="utf-8")
        self.assertIn('"redis_preflight.yml"', worker)
        self.assertIn('"redis_rollback.yml"', worker)
        self.assertIn("return preflight_result, None", worker)
        self.assertIn("预检失败，未开始变更", worker)
        self.assertIn('process_env["ANSIBLE_ROLES_PATH"]', worker)
        self.assertIn("ssh_password_encrypted", worker)
        self.assertNotIn("ssh_private_key", worker)

    def test_host_keys_are_persistent_and_first_use_only(self):
        probe = (ROOT / "backend" / "app" / "host_probe.py").read_text(encoding="utf-8")
        self.assertIn('Path(settings().packages_dir) / ".ssh_known_hosts"', probe)
        self.assertIn("StrictHostKeyChecking=accept-new", probe)
        self.assertIn('"ansible_ssh_retries": 3', probe)

    def test_migrations_run_before_api_and_worker_waits_for_health(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("alembic upgrade head", compose)
        self.assertIn("condition: service_healthy", compose)
        self.assertTrue((ROOT / "backend" / "migrations" / "env.py").is_file())

    def test_ansible_does_not_invoke_recursive_rm(self):
        ansible_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "ansible").rglob("*")
            if path.is_file()
        )
        self.assertIsNone(re.search(r"(^|\s)rm\s+(-[A-Za-z]*r|--recursive)", ansible_text))

    def test_backend_build_uses_tsinghua_pypi(self):
        dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("https://pypi.tuna.tsinghua.edu.cn/simple", dockerfile)
        self.assertIn("sshpass", dockerfile)


if __name__ == "__main__":
    unittest.main()
