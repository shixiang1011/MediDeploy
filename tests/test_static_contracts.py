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
        self.assertIn('@app.put("/api/hosts/{host_id}")', backend)
        self.assertIn('@app.post("/api/deployments/{deployment_id}/rollback"', backend)

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
        config = (ROOT / "backend" / "app" / "config.py").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        frontend = (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
        self.assertIn('SOURCE = "source"', models)
        self.assertIn('BINARY = "binary"', models)
        self.assertIn('(".tar.gz", ".tgz")', api)
        self.assertIn('upload_tmp_dir: str = "/opt/spmp/packages/tmp"', config)
        self.assertIn('os.environ.setdefault("TMPDIR"', api)
        self.assertIn("TMPDIR: /opt/spmp/packages/tmp", compose)
        self.assertIn('accept=".tar.gz,.tgz,.gz,application/gzip,application/x-gzip"', frontend)
        self.assertIn("name.endsWith('.tar.gz') || name.endsWith('.tgz')", frontend)
        self.assertIn("filename.endsWith('.tar.gz') || filename.endsWith('.tgz')", frontend)
        self.assertIn("uploadProgress", frontend)
        self.assertIn("onUploadProgress", frontend)
        self.assertIn("upload-progress-track", frontend)
        self.assertIn("上传成功", frontend)
        self.assertNotIn("sha256", models + api.lower())

    def test_manual_refresh_has_visible_feedback(self):
        frontend = (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
        self.assertIn("refreshing: false", frontend)
        self.assertIn("async refresh(manual = false)", frontend)
        self.assertIn('@click="refresh(true)"', frontend)
        self.assertIn("刷新中...", frontend)
        self.assertIn("spinner", frontend)
        self.assertIn("refreshPulse", frontend)

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
        self.assertIn("Start Redis source build (progress is checked every 10 seconds)", tasks)
        self.assertIn("Wait for Redis source build (30 minute limit)", tasks)
        self.assertIn("custom_redis_conf | default('', true) | length == 0", tasks)
        self.assertIn("custom_redis_conf | default('', true) | length > 0", tasks)
        self.assertIn("- runuser", tasks)
        self.assertEqual(tasks.count('ansible_async_dir: "{{ redis_install_path }}/.ansible_async"'), 2)
        self.assertIn("async: 1800", tasks)
        self.assertIn("delay: 10", tasks)
        self.assertIn("Verify every Redis client port", playbook)
        self.assertIn("Verify every Redis Cluster Bus port", playbook)
        self.assertIn("Verify Redis Cluster reaches the healthy state", playbook)

        rollback = (
            ROOT / "ansible" / "playbooks" / "redis_rollback.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("Revoke this task service user's parent traversal ACLs", rollback)
        self.assertIn("Stop exact task-owned build or service processes", rollback)
        self.assertIn("- pkill", rollback)
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
        self.assertIn("def recover_interrupted_tasks()", worker)
        self.assertIn("def run_rollback_task(", worker)
        self.assertIn("rollback_only=True", worker)
        self.assertIn("TaskStatus.ROLLBACK_QUEUED", worker)
        self.assertIn("TaskStatus.ROLLING_BACK", worker)
        self.assertIn("recover_interrupted_tasks()", worker)
        self.assertIn("Worker 异常后的保护性回滚失败", worker)
        self.assertIn("预检失败，未开始变更", worker)
        self.assertIn('process_env["ANSIBLE_ROLES_PATH"]', worker)
        self.assertIn("ssh_password_encrypted", worker)
        self.assertNotIn("ssh_private_key", worker)

    def test_host_keys_are_persistent_and_first_use_only(self):
        probe = (ROOT / "backend" / "app" / "host_probe.py").read_text(encoding="utf-8")
        self.assertIn('Path(settings().packages_dir) / ".ssh_known_hosts"', probe)
        self.assertIn("StrictHostKeyChecking=accept-new", probe)
        self.assertIn('"ansible_ssh_retries": 3', probe)
        self.assertIn('"ansible_python_interpreter": "/usr/bin/python3"', probe)
        self.assertIn("ControlMaster=no", probe)
        self.assertIn("ControlPersist=no", probe)

    def test_deployment_uses_fresh_task_scoped_persistent_ssh_pool(self):
        worker = (ROOT / "backend" / "app" / "worker.py").read_text(encoding="utf-8")
        self.assertIn('"ANSIBLE_SSH_CONTROL_PATH_DIR"', worker)
        self.assertIn("ssh-control", worker)
        self.assertIn("ControlMaster=auto", worker)
        self.assertIn("ControlPersist=15m", worker)

    def test_migrations_run_before_api_and_worker_waits_for_health(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("alembic upgrade head", compose)
        self.assertIn("condition: service_healthy", compose)
        self.assertTrue((ROOT / "backend" / "migrations" / "env.py").is_file())
        rollback_migration = (
            ROOT
            / "backend"
            / "migrations"
            / "versions"
            / "20260724_0003_rollback_queued_status.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"ROLLBACK_QUEUED"', rollback_migration)

    def test_elasticsearch_optional_heap_is_null_safe(self):
        tasks = (
            ROOT / "ansible" / "roles" / "elasticsearch" / "tasks" / "main.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("Write JVM heap settings when configured", tasks)
        self.assertIn("es_heap_size | default('', true) | length > 0", tasks)
        self.assertNotIn("es_heap_size | default('') | length > 0", tasks)

    def test_elasticsearch_keystore_is_readable_by_service_user(self):
        tasks = (
            ROOT / "ansible" / "roles" / "elasticsearch" / "tasks" / "main.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("Ensure Elasticsearch keystore is owned by the service user", tasks)
        self.assertIn('path: "{{ es_config_path }}/elasticsearch.keystore"', tasks)
        self.assertIn('owner: "{{ es_service_user }}"', tasks)
        self.assertIn("mode: '0600'", tasks)

    def test_failed_tasks_can_retry_exact_rollback_before_redeployment(self):
        models = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
        api = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        frontend = (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
        self.assertIn('ROLLBACK_QUEUED = "rollback_queued"', models)
        self.assertIn("deployment.status = TaskStatus.ROLLBACK_QUEUED", api)
        self.assertIn("重新回滚", frontend)
        self.assertIn("更新凭据", frontend)
        self.assertIn("needsRollback(task)", frontend)

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

    def test_catalogs_support_search_and_safe_deletion(self):
        api = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        frontend = (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
        for route in (
            '@app.delete("/api/hosts/{host_id}"',
            '@app.delete("/api/packages/{package_id}"',
            '@app.delete("/api/deployments/{deployment_id}"',
        ):
            self.assertIn(route, api)
        self.assertIn("q: str | None = None", api)
        self.assertIn("package_path.unlink(missing_ok=True)", api)
        self.assertIn("package_path.parent != package_root", api)
        self.assertNotIn("subprocess.run([\"rm\"", api)
        for label in ("搜索服务器", "搜索软件包", "搜索部署任务", "删除"):
            self.assertIn(label, frontend)
        self.assertIn("window.setInterval(() => this.refreshActiveList(), 15000)", frontend)
        self.assertIn("Host.enabled.is_(True)", api)
        self.assertIn("Deployment.deleted_at.is_(None)", api)

    def test_successful_deployment_has_repeatable_word_report(self):
        api = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        worker = (ROOT / "backend" / "app" / "worker.py").read_text(encoding="utf-8")
        report = (ROOT / "backend" / "app" / "report_generator.py").read_text(
            encoding="utf-8"
        )
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
        migration = (
            ROOT
            / "backend"
            / "migrations"
            / "versions"
            / "20260724_0004_catalog_deletion_and_reports.py"
        ).read_text(encoding="utf-8")

        self.assertIn('@app.get("/api/deployments/{deployment_id}/report")', api)
        self.assertIn("FileResponse(", api)
        self.assertIn('"download"', api)
        self.assertIn("build_report(", worker)
        self.assertIn("Word 交付报告已生成", worker)
        self.assertIn("敏感信息提示", report)
        self.assertIn("Redis 密码", report)
        self.assertIn("节点与端口", report)
        self.assertIn("os.replace(temporary_path, output_path)", report)
        self.assertIn("python-docx==", requirements)
        self.assertIn("reports-data:/opt/spmp/reports", compose)
        self.assertIn('"report_snapshot"', migration)
        self.assertIn("下载 Word 报告", (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8"))

    def test_elasticsearch_standalone_and_cluster_deployment_contract(self):
        schemas = (ROOT / "backend" / "app" / "schemas.py").read_text(encoding="utf-8")
        api = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        worker = (ROOT / "backend" / "app" / "worker.py").read_text(encoding="utf-8")
        frontend = (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
        report = (ROOT / "backend" / "app" / "report_generator.py").read_text(
            encoding="utf-8"
        )
        playbooks = {
            path.name: path.read_text(encoding="utf-8")
            for path in (ROOT / "ansible" / "playbooks").glob("elasticsearch*.yml")
        }
        role_tasks = (
            ROOT / "ansible" / "roles" / "elasticsearch" / "tasks" / "main.yml"
        ).read_text(encoding="utf-8")
        es_config = (
            ROOT
            / "ansible"
            / "roles"
            / "elasticsearch"
            / "templates"
            / "elasticsearch.yml.j2"
        ).read_text(encoding="utf-8")
        es_service = (
            ROOT
            / "ansible"
            / "roles"
            / "elasticsearch"
            / "templates"
            / "elasticsearch.service.j2"
        ).read_text(encoding="utf-8")

        self.assertIn("ElasticsearchDeploymentConfig", schemas)
        self.assertIn("ElasticsearchInstance", schemas)
        self.assertIn('"elasticsearch", "name": "Elasticsearch", "available": True', api)
        self.assertIn('PackageType.BINARY', api)
        self.assertIn('config["elastic_password"] = encrypt', api)
        self.assertIn('safe_config.pop("elastic_password", None)', api)
        self.assertIn('call_elasticsearch_ansible', worker)
        self.assertIn('"elasticsearch_preflight.yml"', worker)
        self.assertIn('"elasticsearch_rollback.yml"', worker)
        self.assertIn("es_elastic_password", worker)
        self.assertIn("Elasticsearch", frontend)
        self.assertIn("elastic 密码", frontend)
        self.assertIn("Transport 端口", frontend)
        self.assertIn("availableWizardPackages", frontend)
        self.assertIn("Elasticsearch 密码", report)
        self.assertIn("_build_elasticsearch_report", report)

        self.assertEqual(
            set(playbooks),
            {
                "elasticsearch.yml",
                "elasticsearch_preflight.yml",
                "elasticsearch_rollback.yml",
            },
        )
        self.assertIn("Require official binary Elasticsearch package", playbooks["elasticsearch_preflight.yml"])
        self.assertIn("Verify Elasticsearch cluster reaches a usable health state", playbooks["elasticsearch.yml"])
        self.assertIn("Restore previous vm.max_map_count value", playbooks["elasticsearch_rollback.yml"])
        self.assertIn("elasticsearch-certutil", role_tasks)
        self.assertIn("bootstrap.password", role_tasks)
        self.assertIn("vm.max_map_count=655350", role_tasks)
        self.assertIn("xpack.security.transport.ssl.enabled", es_config)
        self.assertIn("discovery.seed_hosts", es_config)
        self.assertIn("cluster.initial_master_nodes", es_config)
        self.assertIn("ES_PATH_CONF", es_service)
        self.assertIn("User={{ es_service_user }}", es_service)


if __name__ == "__main__":
    unittest.main()
