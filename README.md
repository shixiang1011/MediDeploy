# SP MediDeploy Platform（SPMP）

面向离线/内网服务器的中间件部署平台。首个可用模块为 Redis Cluster，后续可按相同模式扩展 Nacos、Nexus、MinIO 等组件。

## 已实现的 Redis MVP

- FastAPI + Vue 3 管理后台，Docker Compose 一键运行
- 多租户隔离、内置管理员、角色模型、审计日志
- SSH 资产录入（用户名、端口、私钥）、操作系统/架构信息
- Web 管理员上传 Redis 源码包；Redis `6.2.17` 作为首期预置推荐版本
- Redis Cluster 自定义节点数、副本数、端口、安装/数据/日志/配置目录
- 用户优先的 `redis.conf` 编辑；未编辑时由默认模板生成
- Ansible 部署、预检、日志持久化、任务取消和失败自动回滚
- 回滚只处理该次任务在资源清单中登记的 systemd 单元、配置、安装/数据/日志目录；既有路径、既有 Redis 服务和既有数据一律拒绝覆盖
- 本地登录和 LDAP/AD 配置接口（LDAP 连接信息经应用密钥加密保存）

## 启动

1. 复制 `.env.example` 为 `.env`，设置全部密码和 `SECRET_KEY`。
2. 执行 `docker compose up -d --build`。
3. 访问 `http://<平台地址>:8080`，使用 `.env` 中的 `SPMP_BOOTSTRAP_ADMIN` 登录。
4. 在“软件包”页面上传 `redis-6.2.17.tar.gz`（或其他 Redis 源码包），再创建部署任务。平台不从互联网下载软件包。

首次登录使用本地管理员；生产环境应立即修改密码并配置 HTTPS 反向代理。

### 平台自身的离线安装

Redis 的目标机部署不访问互联网。若 SPMP 管理平台所在环境也完全离线，请在一台可联网的构建机执行一次 `docker compose build`，通过 `docker save` 导出构建出的 `api`、`worker`、`web` 镜像以及 `mysql:8.4`，再在离线环境执行 `docker load` 和 `docker compose up -d`。Docker 构建阶段会下载 Python、Node 和系统依赖，因此不能直接在完全离线的空白环境构建。

## 源码编译前置条件

源码包可用于内置版本或管理员上传版本。提交任务前，执行人员必须确保每个目标机已离线安装以下命令：`python3`、`tar`、`make`、`gcc`、`sudo`、`systemctl`。平台会先执行预检，缺项时不会开始部署；由于内网环境，平台不会尝试从目标操作系统的软件源下载安装依赖。

## Redis Cluster 约束

- 节点数至少为 `3`。
- `副本数 + 1` 必须能整除节点数，例如 6 节点可选 0 或 1 副本。
- 所有节点必须互通 Redis 端口及其 Cluster bus 端口（默认是 Redis 端口 + 10000）。防火墙由环境管理员预先处理。
- 平台以任务 UUID 作为资源所有权标识。发现目标路径非空、服务名已存在或端口被监听时，任务失败且不修改机器。

## 安全说明

不使用 `rm` 进行回滚。Ansible 的 `file: state=absent` 仅对本任务登记且创建成功的精确目录执行；先停止由本任务创建的 systemd 服务。该策略不能、也不会处理部署前已经存在的任何内容。

## 目录

`backend/` API 和 Worker；`frontend/` Vue 3 后台；`ansible/` Redis 角色。上传的软件包存储于 Docker 持久卷，不需要也不应手工放入宿主机目录。
