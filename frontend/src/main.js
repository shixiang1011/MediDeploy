// This entry uses an inline template, so it must import Vue's compiler-enabled
// build. The default Vite Vue export is runtime-only and would mount a comment
// node instead of rendering the application.
import { createApp } from 'vue/dist/vue.esm-bundler.js'
import axios from 'axios'
import './style.css'

const api = axios.create({ baseURL: '/api/' })
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('spmp-token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

const emptyHost = () => ({
  name: '', address: '', ssh_port: 22, ssh_user: 'root', ssh_private_key: '',
  os_family: 'Rocky', os_version: '9', architecture: 'x86_64',
})

createApp({
  data() {
    return {
      token: localStorage.getItem('spmp-token') || '',
      user: null,
      error: '',
      notice: '',
      active: 'dashboard',
      loginForm: { username: 'admin', password: '' },
      hosts: [], packages: [], deployments: [], logs: [], selectedDeployment: null,
      hostForm: emptyHost(),
      packageForm: { version: '6.2.17', file: null },
      tenantForm: { name: '' },
      userForm: { username: '', password: '', tenant_id: '', role: 'tenant_admin' },
      deployForm: {
        name: '', package_id: '', node_ids: [], replicas: 1, redis_port: 6379,
        install_dir: '/opt/middleware/redis', data_dir: '/data/redis',
        log_dir: '/var/log/middleware/redis', config_dir: '/etc/middleware/redis',
        redis_password: '', maxmemory: '', maxmemory_policy: 'noeviction',
        appendonly: true, custom_redis_conf: '',
      },
    }
  },
  computed: {
    isAdmin() { return this.user && this.user.role === 'super_admin' },
    isOperator() { return this.user && ['super_admin', 'tenant_admin', 'operator'].includes(this.user.role) },
  },
  methods: {
    roleLabel(role) {
      return {
        super_admin: '超级管理员',
        tenant_admin: '租户管理员',
        operator: '操作员',
        auditor: '审计员',
      }[role] || role
    },
    statusLabel(status) {
      return {
        queued: '等待执行',
        running: '执行中',
        succeeded: '成功',
        failed: '失败',
        rolling_back: '回滚中',
        rolled_back: '已回滚',
        cancelled: '已取消',
      }[status] || status
    },
    async request(method, url, body, config = {}) {
      try {
        const response = await api.request({ method, url, data: body, ...config })
        return response.data
      } catch (error) {
        this.error = error.response?.data?.detail || error.message || '请求失败'
        throw error
      }
    },
    async login() {
      this.error = ''
      try {
        const data = await this.request('post', 'auth/login', this.loginForm)
        this.token = data.access_token
        this.user = data.user
        localStorage.setItem('spmp-token', this.token)
        await this.refresh()
      } catch (_) {}
    },
    logout() {
      localStorage.removeItem('spmp-token')
      this.token = ''
      this.user = null
    },
    async refresh() {
      if (!this.token) return
      try {
        this.user = await this.request('get', 'me')
        await Promise.all([this.loadHosts(), this.loadPackages(), this.loadDeployments()])
      } catch (_) {
        this.logout()
      }
    },
    async loadHosts() { this.hosts = await this.request('get', 'hosts') },
    async loadPackages() {
      this.packages = await this.request('get', 'packages')
      if (!this.deployForm.package_id && this.packages.length) this.deployForm.package_id = this.packages[0].id
    },
    async loadDeployments() { this.deployments = await this.request('get', 'deployments') },
    async createHost() {
      const item = await this.request('post', 'hosts', this.hostForm)
      this.hosts.unshift(item)
      this.hostForm = emptyHost()
      this.notice = '服务器资产已保存，SSH 私钥已加密存储。'
    },
    chooseFile(event) { this.packageForm.file = event.target.files[0] },
    async uploadPackage() {
      if (!this.packageForm.file) { this.error = '请先选择 Redis 源码压缩包。'; return }
      const form = new FormData()
      form.append('file', this.packageForm.file)
      const item = await this.request('post', `packages/redis?version=${encodeURIComponent(this.packageForm.version)}`, form)
      this.packages.unshift(item)
      this.deployForm.package_id = item.id
      this.notice = 'Redis 源码包上传成功，SHA-256 校验值已记录。'
    },
    toggleNode(id) {
      const index = this.deployForm.node_ids.indexOf(id)
      if (index >= 0) this.deployForm.node_ids.splice(index, 1)
      else this.deployForm.node_ids.push(id)
    },
    async createDeployment() {
      const config = { ...this.deployForm }
      delete config.name
      delete config.package_id
      config.maxmemory = config.maxmemory || null
      config.custom_redis_conf = config.custom_redis_conf || null
      try {
        const item = await this.request('post', 'deployments/redis', {
          name: this.deployForm.name, package_id: this.deployForm.package_id, config,
        })
        this.deployments.unshift(item)
        this.notice = '部署任务已提交，修改目标服务器前将先执行完整预检。'
        this.active = 'tasks'
      } catch (_) {}
    },
    async showLogs(task) {
      this.selectedDeployment = task
      this.logs = await this.request('get', `deployments/${task.id}/logs`)
    },
    async retry(task) {
      await this.request('post', `deployments/${task.id}/retry`)
      this.notice = '重试任务已创建。'
      await this.loadDeployments()
    },
    async createTenant() {
      const item = await this.request('post', 'admin/tenants', this.tenantForm)
      this.userForm.tenant_id = item.id
      this.tenantForm.name = ''
      this.notice = `租户“${item.name}”已创建。`
    },
    async createUser() {
      await this.request('post', 'admin/users', this.userForm)
      this.userForm.username = ''
      this.userForm.password = ''
      this.notice = '用户创建成功。'
    },
  },
  mounted() { this.refresh() },
  template: `
    <main v-if="!token" class="login-shell">
      <section class="login-card">
        <div class="brand-mark">SP</div>
        <h1>SP MediDeploy</h1>
        <p>安全、可审计、支持离线环境的中间件部署平台</p>
        <form @submit.prevent="login">
          <label>用户名<input v-model="loginForm.username" autocomplete="username" /></label>
          <label>密码<input v-model="loginForm.password" type="password" autocomplete="current-password" autofocus /></label>
          <button>登录平台</button>
        </form>
        <small>初始密码不是“SPMP_BOOTSTRAP_PASSWORD”这段文字，请使用服务器 .env 文件中该变量的实际值。</small>
        <p v-if="error" class="error">{{ error }}</p>
      </section>
    </main>
    <main v-else class="app-shell">
      <aside>
        <div class="brand"><span>SP</span><div><strong>SP MediDeploy</strong><small>中间件部署平台</small></div></div>
        <nav>
          <button :class="{active: active === 'dashboard'}" @click="active = 'dashboard'">运行概览</button>
          <button :class="{active: active === 'hosts'}" @click="active = 'hosts'">服务器资产</button>
          <button :class="{active: active === 'packages'}" @click="active = 'packages'">软件包管理</button>
          <button :class="{active: active === 'redis'}" @click="active = 'redis'">Redis 集群</button>
          <button :class="{active: active === 'tasks'}" @click="active = 'tasks'">部署任务</button>
          <button v-if="isAdmin" :class="{active: active === 'admin'}" @click="active = 'admin'">平台管理</button>
        </nav>
        <div class="profile"><b>{{ user.username }}</b><span>{{ roleLabel(user.role) }}</span><a @click="logout">退出登录</a></div>
      </aside>
      <section class="content">
        <header><div><h1>SP MediDeploy</h1><p>Redis 6.2.17 部署管理</p></div><button class="secondary" @click="refresh">刷新数据</button></header>
        <p v-if="notice" class="notice">{{ notice }}</p><p v-if="error" class="error">{{ error }}</p>

        <section v-if="active === 'dashboard'" class="dashboard">
          <article><span>服务器</span><strong>{{ hosts.length }}</strong><small>当前可用目标节点</small></article>
          <article><span>软件包</span><strong>{{ packages.length }}</strong><small>已上传的 Redis 源码包</small></article>
          <article><span>部署任务</span><strong>{{ deployments.length }}</strong><small>包含部署与重试记录</small></article>
          <article><span>安全机制</span><strong>预检</strong><small>仅修改当前任务创建的资源</small></article>
          <div class="panel wide"><h2>使用流程</h2><ol><li>登记可通过 SSH 访问的服务器。</li><li>上传离线 Redis 源码压缩包。</li><li>选择节点并提交 Redis 集群部署任务。</li><li>查看任务日志；失败时仅回滚本次任务创建的资源。</li></ol></div>
        </section>

        <section v-if="active === 'hosts'" class="two-col">
          <form class="panel form" @submit.prevent="createHost"><h2>登记服务器</h2>
            <label>服务器名称<input v-model="hostForm.name" required placeholder="redis-node-01" /></label>
            <label>IP 地址或主机名<input v-model="hostForm.address" required placeholder="10.0.0.11" /></label>
            <div class="row"><label>SSH 端口<input v-model.number="hostForm.ssh_port" type="number" required /></label><label>SSH 用户<input v-model="hostForm.ssh_user" required /></label></div>
            <div class="row"><label>操作系统<select v-model="hostForm.os_family"><option>openEuler</option><option>银河麒麟 V10 SP3</option><option>Rocky</option><option>Ubuntu</option><option>CentOS</option></select></label><label>系统版本<input v-model="hostForm.os_version" /></label></div>
            <label>SSH 私钥<textarea v-model="hostForm.ssh_private_key" required placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></label>
            <button :disabled="!isOperator">加密并保存</button>
          </form>
          <div class="panel"><h2>已登记服务器</h2><table><thead><tr><th>名称</th><th>地址</th><th>操作系统</th><th>架构</th></tr></thead><tbody><tr v-for="host in hosts" :key="host.id"><td>{{ host.name }}</td><td>{{ host.address }}:{{ host.ssh_port }}</td><td>{{ host.os_family }} {{ host.os_version }}</td><td>{{ host.architecture }}</td></tr><tr v-if="!hosts.length"><td colspan="4" class="muted">暂无服务器资产。</td></tr></tbody></table></div>
        </section>

        <section v-if="active === 'packages'" class="two-col">
          <form class="panel form" @submit.prevent="uploadPackage"><h2>上传 Redis 源码包</h2><p class="muted">支持 tar.gz 或 tgz 格式，上传后保存在平台持久化存储中。</p><label>Redis 版本<input v-model="packageForm.version" required /></label><label>源码压缩包<input type="file" accept=".tar.gz,.tgz" @change="chooseFile" required /></label><button :disabled="!isOperator">上传并记录 SHA-256</button></form>
          <div class="panel"><h2>软件包列表</h2><table><thead><tr><th>版本</th><th>文件名</th><th>SHA-256</th><th>架构</th></tr></thead><tbody><tr v-for="pkg in packages" :key="pkg.id"><td>{{ pkg.version }}</td><td>{{ pkg.filename }}</td><td class="hash">{{ pkg.sha256 }}</td><td>{{ pkg.architecture }}</td></tr><tr v-if="!packages.length"><td colspan="4" class="muted">请先上传 Redis 6.2.17 源码包。</td></tr></tbody></table></div>
        </section>

        <section v-if="active === 'redis'" class="panel form deployment-form"><h2>创建 Redis 集群部署任务</h2><p class="muted">所有目标路径必须尚未存在。填写自定义 redis.conf 后，将完全覆盖平台生成的默认配置。</p>
          <div class="row"><label>任务名称<input v-model="deployForm.name" required /></label><label>源码包<select v-model="deployForm.package_id" required><option disabled value="">请选择软件包</option><option v-for="pkg in packages" :key="pkg.id" :value="pkg.id">Redis {{ pkg.version }} - {{ pkg.filename }}</option></select></label></div>
          <h3>目标节点（至少 3 个）</h3><div class="host-picker"><label v-for="host in hosts" :key="host.id" :class="{picked: deployForm.node_ids.includes(host.id)}"><input type="checkbox" :checked="deployForm.node_ids.includes(host.id)" @change="toggleNode(host.id)" /><b>{{ host.name }}</b><span>{{ host.address }}</span></label><p v-if="!hosts.length" class="muted">请先登记服务器。</p></div>
          <div class="row"><label>副本数<select v-model.number="deployForm.replicas"><option :value="0">0</option><option :value="1">1（推荐）</option><option :value="2">2</option></select></label><label>Redis 端口<input v-model.number="deployForm.redis_port" type="number" min="1024" max="65535" /></label><label>Redis 密码<input v-model="deployForm.redis_password" type="password" required minlength="8" /></label></div>
          <div class="grid4"><label>安装目录<input v-model="deployForm.install_dir" /></label><label>数据目录<input v-model="deployForm.data_dir" /></label><label>日志目录<input v-model="deployForm.log_dir" /></label><label>配置目录<input v-model="deployForm.config_dir" /></label></div>
          <div class="row"><label>AOF<select v-model="deployForm.appendonly"><option :value="true">开启</option><option :value="false">关闭</option></select></label><label>最大内存<input v-model="deployForm.maxmemory" placeholder="可选，例如 8gb" /></label><label>淘汰策略<select v-model="deployForm.maxmemory_policy"><option>noeviction</option><option>allkeys-lru</option><option>volatile-lru</option><option>allkeys-lfu</option></select></label></div>
          <label>自定义 redis.conf（可选）<textarea v-model="deployForm.custom_redis_conf" rows="10" placeholder="port 6379&#10;cluster-enabled yes&#10;..."></textarea></label><button @click="createDeployment" :disabled="!isOperator">提交部署任务</button>
        </section>

        <section v-if="active === 'tasks'" class="panel"><h2>部署任务与审计日志</h2><table><thead><tr><th>任务</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="task in deployments" :key="task.id"><td><b>{{ task.name }}</b><small>{{ task.id }}</small></td><td><span class="status" :class="task.status">{{ statusLabel(task.status) }}</span></td><td>{{ new Date(task.created_at).toLocaleString() }}</td><td><button class="link" @click="showLogs(task)">查看日志</button><button v-if="['failed', 'rolled_back'].includes(task.status) && isOperator" class="link" @click="retry(task)">重试</button></td></tr><tr v-if="!deployments.length"><td colspan="4" class="muted">暂无部署任务。</td></tr></tbody></table><div v-if="selectedDeployment" class="logs"><div><h3>{{ selectedDeployment.name }} 日志</h3><button class="link" @click="showLogs(selectedDeployment)">刷新</button></div><pre v-for="line in logs" :key="line.id">[{{ new Date(line.created_at).toLocaleTimeString() }}] {{ line.level }} {{ line.message }}</pre></div></section>

        <section v-if="active === 'admin'" class="two-col"><form class="panel form" @submit.prevent="createTenant"><h2>创建租户</h2><label>租户名称<input v-model="tenantForm.name" required /></label><button>创建租户</button></form><form class="panel form" @submit.prevent="createUser"><h2>创建本地用户</h2><label>用户名<input v-model="userForm.username" required /></label><label>密码<input v-model="userForm.password" type="password" required /></label><label>租户 ID<input v-model="userForm.tenant_id" required /></label><label>角色<select v-model="userForm.role"><option value="tenant_admin">租户管理员</option><option value="operator">操作员</option><option value="auditor">审计员</option></select></label><button>创建用户</button></form></section>
      </section>
    </main>
  `,
}).mount('#app')
