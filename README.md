# SP MediDeploy Platform（SPMP）

SPMP 是面向内网和离线服务器的通用中间件部署平台。平台采用 FastAPI、Vue 3、MySQL 和 Ansible，并通过 Docker Compose 运行。

当前版本完整实现 Redis 普通单机和 Redis Cluster 部署。Nacos、MinIO、RabbitMQ、Kafka、Elasticsearch、Nginx、Nexus 已进入组件注册表，后续可按相同插件边界增加部署实现。

## 当前能力

- 通用服务器资产：SSH 用户名/密码登录，可选 sudo 提权和独立 sudo 密码。
- 保存服务器前检查 SSH、sudo、Python 3、操作系统、x86_64 架构和磁盘空间。
- 通用软件包仓库：支持各中间件的源码包和已编译二进制包。
- 软件包格式仅允许 `.tar.gz` 和 `.tgz`，不计算 SHA-256。
- Redis 单机模式：部署一个不启用 Cluster 的普通 Redis 实例。
- Redis Cluster：自定义主节点数、副本数和实例位置。
- 同一服务器允许部署多个 Redis 实例，但端口和任务目录不得重复或互相嵌套。
- 每个实例可独立设置端口、安装目录、数据目录、日志目录、配置目录和 `redis.conf`。
- 类云服务器购买流程的分步向导：基础信息、拓扑配置、运行参数、确认提交。
- 全部实例先统一预检；任一预检失败时不开始变更。
- 失败时执行独立回滚 playbook，只清理带任务所有权标记的精确资源。
- 多租户、角色、LDAP 和审计数据模型继续保留，平台管理界面暂时隐藏。
- Alembic 管理数据库版本。

## 目标服务器前置条件

所有目标服务器必须预先准备：

- SSH 密码登录。
- root 用户，或具备 sudo 权限的普通用户。
- Python 3、`tar`、`ss`、`systemctl`。
- 使用源码包时还需要 `make` 和 `gcc`。
- 节点间开放 Redis 服务端口和 Cluster Bus 端口（Redis 端口 + 10000）。

平台不会在目标机上联网安装依赖。依赖不完整时预检失败，不会开始部署。

## 启动

1. 复制 `.env.example` 为 `.env`，设置 MySQL 密码、`SECRET_KEY` 和初始管理员密码。
2. 执行：

   ```bash
   docker-compose up -d --build
   ```

3. 访问 `http://<平台地址>:8080`。
4. 使用 `.env` 中 `SPMP_BOOTSTRAP_ADMIN` 和 `SPMP_BOOTSTRAP_PASSWORD` 的实际值登录。

API 容器启动时自动执行 `alembic upgrade head`。Worker 会等待 API 健康后再启动。

## 软件包约定

源码包应包含一个 `redis-*` 根目录，例如官方 `redis-6.2.17.tar.gz`。

已编译二进制包可包含任意目录结构，但必须能够递归找到：

- `redis-server`
- `redis-cli`

## 安全与回滚

- SSH 密码、sudo 密码、Redis 密码使用平台 `SECRET_KEY` 派生密钥加密保存。
- 首次连接目标服务器时采用 TOFU 记录 SSH 主机密钥；之后主机密钥变化会被拒绝。
- 不使用 `rm` 执行部署回滚。
- Ansible `file: state=absent` 只处理本任务创建且存在任务所有权标记的精确路径。
- 目标路径、端口或服务已存在时，预检直接拒绝部署。
- 自定义 `redis.conf` 优先级最高，用户需确保其中的端口和 Cluster 配置与向导一致。

## 测试

无需安装业务依赖即可运行静态契约测试：

```bash
python -m unittest discover -s tests -v
```

正式验收还包括 Docker 镜像构建、Alembic 初始化、API 登录、Vue 页面渲染和 Ansible `--syntax-check`。
