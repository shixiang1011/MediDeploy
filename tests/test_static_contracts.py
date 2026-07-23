import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticContractTests(unittest.TestCase):
    def test_nginx_preserves_fastapi_api_prefix(self):
        config = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
        self.assertIn("location /api/", config)
        self.assertIn("proxy_pass http://api:8000;", config)
        self.assertNotIn("proxy_pass http://api:8000/;", config)

    def test_frontend_login_matches_backend_route(self):
        frontend = (ROOT / "frontend" / "src" / "main.js").read_text(encoding="utf-8")
        backend = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("baseURL: '/api/'", frontend)
        self.assertIn("'auth/login'", frontend)
        self.assertIn('@app.post("/api/auth/login")', backend)

    def test_frontend_uses_compiler_build_and_simplified_chinese(self):
        frontend = (ROOT / "frontend" / "src" / "main.js").read_text(encoding="utf-8")
        self.assertIn("vue/dist/vue.esm-bundler.js", frontend)
        self.assertIn("用户名", frontend)
        self.assertIn("登录平台", frontend)
        self.assertIn("服务器资产", frontend)
        self.assertIn("部署任务", frontend)

    def test_redis_binary_directory_is_created_before_copy(self):
        tasks = (ROOT / "ansible" / "roles" / "redis" / "tasks" / "main.yml").read_text(encoding="utf-8")
        create_index = tasks.index('path: "{{ redis_install_path }}/bin"')
        copy_index = tasks.index("Copy built Redis binaries under task-owned path")
        self.assertLess(create_index, copy_index)

    def test_ansible_does_not_invoke_rm(self):
        ansible_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "ansible").rglob("*")
            if path.is_file()
        )
        self.assertIsNone(re.search(r"(^|\s)rm\s+(-[A-Za-z]*r|--recursive)", ansible_text))

    def test_backend_build_uses_tsinghua_pypi(self):
        dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("https://pypi.tuna.tsinghua.edu.cn/simple", dockerfile)


if __name__ == "__main__":
    unittest.main()
