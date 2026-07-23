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
    async request(method, url, body, config = {}) {
      try {
        const response = await api.request({ method, url, data: body, ...config })
        return response.data
      } catch (error) {
        this.error = error.response?.data?.detail || error.message || 'Request failed'
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
      this.notice = 'Server asset saved. The SSH key is encrypted at rest.'
    },
    chooseFile(event) { this.packageForm.file = event.target.files[0] },
    async uploadPackage() {
      if (!this.packageForm.file) { this.error = 'Select a Redis source archive first.'; return }
      const form = new FormData()
      form.append('file', this.packageForm.file)
      const item = await this.request('post', `packages/redis?version=${encodeURIComponent(this.packageForm.version)}`, form)
      this.packages.unshift(item)
      this.deployForm.package_id = item.id
      this.notice = 'Redis source package uploaded and its SHA-256 was recorded.'
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
        this.notice = 'Deployment task submitted. Preflight checks run before any target is changed.'
        this.active = 'tasks'
      } catch (_) {}
    },
    async showLogs(task) {
      this.selectedDeployment = task
      this.logs = await this.request('get', `deployments/${task.id}/logs`)
    },
    async retry(task) {
      await this.request('post', `deployments/${task.id}/retry`)
      this.notice = 'Retry task created.'
      await this.loadDeployments()
    },
    async createTenant() {
      const item = await this.request('post', 'admin/tenants', this.tenantForm)
      this.userForm.tenant_id = item.id
      this.tenantForm.name = ''
      this.notice = `Tenant ${item.name} created.`
    },
    async createUser() {
      await this.request('post', 'admin/users', this.userForm)
      this.userForm.username = ''
      this.userForm.password = ''
      this.notice = 'User created.'
    },
  },
  mounted() { this.refresh() },
  template: `
    <main v-if="!token" class="login-shell">
      <section class="login-card">
        <div class="brand-mark">SP</div>
        <h1>SP MediDeploy</h1>
        <p>Secure, auditable, offline middleware deployment.</p>
        <form @submit.prevent="login">
          <label>Username<input v-model="loginForm.username" autocomplete="username" /></label>
          <label>Password<input v-model="loginForm.password" type="password" autocomplete="current-password" autofocus /></label>
          <button>Sign in</button>
        </form>
        <small>The initial password is configured by SPMP_BOOTSTRAP_PASSWORD.</small>
        <p v-if="error" class="error">{{ error }}</p>
      </section>
    </main>
    <main v-else class="app-shell">
      <aside>
        <div class="brand"><span>SP</span><div><strong>SP MediDeploy</strong><small>Middleware Platform</small></div></div>
        <nav>
          <button :class="{active: active === 'dashboard'}" @click="active = 'dashboard'">Dashboard</button>
          <button :class="{active: active === 'hosts'}" @click="active = 'hosts'">Servers</button>
          <button :class="{active: active === 'packages'}" @click="active = 'packages'">Packages</button>
          <button :class="{active: active === 'redis'}" @click="active = 'redis'">Redis Cluster</button>
          <button :class="{active: active === 'tasks'}" @click="active = 'tasks'">Deployment Tasks</button>
          <button v-if="isAdmin" :class="{active: active === 'admin'}" @click="active = 'admin'">Platform Admin</button>
        </nav>
        <div class="profile"><b>{{ user.username }}</b><span>{{ user.role }}</span><a @click="logout">Sign out</a></div>
      </aside>
      <section class="content">
        <header><div><h1>SP MediDeploy</h1><p>Redis 6.2.17 deployment MVP</p></div><button class="secondary" @click="refresh">Refresh</button></header>
        <p v-if="notice" class="notice">{{ notice }}</p><p v-if="error" class="error">{{ error }}</p>

        <section v-if="active === 'dashboard'" class="dashboard">
          <article><span>Servers</span><strong>{{ hosts.length }}</strong><small>Available target nodes</small></article>
          <article><span>Packages</span><strong>{{ packages.length }}</strong><small>Uploaded Redis archives</small></article>
          <article><span>Tasks</span><strong>{{ deployments.length }}</strong><small>Deployment and retry history</small></article>
          <article><span>Safety</span><strong>Preflight</strong><small>Changes only task-owned resources</small></article>
          <div class="panel wide"><h2>Workflow</h2><ol><li>Register SSH-accessible servers.</li><li>Upload an offline Redis source archive.</li><li>Choose nodes and submit a Redis Cluster task.</li><li>Review logs; failed tasks roll back their own created resources.</li></ol></div>
        </section>

        <section v-if="active === 'hosts'" class="two-col">
          <form class="panel form" @submit.prevent="createHost"><h2>Register server</h2>
            <label>Name<input v-model="hostForm.name" required placeholder="redis-node-01" /></label>
            <label>IP or hostname<input v-model="hostForm.address" required placeholder="10.0.0.11" /></label>
            <div class="row"><label>SSH port<input v-model.number="hostForm.ssh_port" type="number" required /></label><label>SSH user<input v-model="hostForm.ssh_user" required /></label></div>
            <div class="row"><label>Operating system<select v-model="hostForm.os_family"><option>openEuler</option><option>银河麒麟 V10 SP3</option><option>Rocky</option><option>Ubuntu</option><option>CentOS</option></select></label><label>Version<input v-model="hostForm.os_version" /></label></div>
            <label>SSH private key<textarea v-model="hostForm.ssh_private_key" required placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></label>
            <button :disabled="!isOperator">Encrypt and save</button>
          </form>
          <div class="panel"><h2>Registered servers</h2><table><thead><tr><th>Name</th><th>Address</th><th>OS</th><th>Architecture</th></tr></thead><tbody><tr v-for="host in hosts" :key="host.id"><td>{{ host.name }}</td><td>{{ host.address }}:{{ host.ssh_port }}</td><td>{{ host.os_family }} {{ host.os_version }}</td><td>{{ host.architecture }}</td></tr><tr v-if="!hosts.length"><td colspan="4" class="muted">No servers registered.</td></tr></tbody></table></div>
        </section>

        <section v-if="active === 'packages'" class="two-col">
          <form class="panel form" @submit.prevent="uploadPackage"><h2>Upload Redis archive</h2><p class="muted">Upload a tar.gz or tgz source archive. It is retained in the platform volume.</p><label>Redis version<input v-model="packageForm.version" required /></label><label>Source archive<input type="file" accept=".tar.gz,.tgz" @change="chooseFile" required /></label><button :disabled="!isOperator">Upload and record SHA-256</button></form>
          <div class="panel"><h2>Package library</h2><table><thead><tr><th>Version</th><th>File</th><th>SHA-256</th><th>Architecture</th></tr></thead><tbody><tr v-for="pkg in packages" :key="pkg.id"><td>{{ pkg.version }}</td><td>{{ pkg.filename }}</td><td class="hash">{{ pkg.sha256 }}</td><td>{{ pkg.architecture }}</td></tr><tr v-if="!packages.length"><td colspan="4" class="muted">Upload Redis 6.2.17 first.</td></tr></tbody></table></div>
        </section>

        <section v-if="active === 'redis'" class="panel form deployment-form"><h2>Create Redis Cluster task</h2><p class="muted">All configured paths must be unused. Custom redis.conf completely replaces the generated default.</p>
          <div class="row"><label>Task name<input v-model="deployForm.name" required /></label><label>Source package<select v-model="deployForm.package_id" required><option disabled value="">Select a package</option><option v-for="pkg in packages" :key="pkg.id" :value="pkg.id">Redis {{ pkg.version }} - {{ pkg.filename }}</option></select></label></div>
          <h3>Target nodes (at least 3)</h3><div class="host-picker"><label v-for="host in hosts" :key="host.id" :class="{picked: deployForm.node_ids.includes(host.id)}"><input type="checkbox" :checked="deployForm.node_ids.includes(host.id)" @change="toggleNode(host.id)" /><b>{{ host.name }}</b><span>{{ host.address }}</span></label><p v-if="!hosts.length" class="muted">Register servers first.</p></div>
          <div class="row"><label>Replicas<select v-model.number="deployForm.replicas"><option :value="0">0</option><option :value="1">1 (recommended)</option><option :value="2">2</option></select></label><label>Redis port<input v-model.number="deployForm.redis_port" type="number" min="1024" max="65535" /></label><label>Redis password<input v-model="deployForm.redis_password" type="password" required minlength="8" /></label></div>
          <div class="grid4"><label>Install directory<input v-model="deployForm.install_dir" /></label><label>Data directory<input v-model="deployForm.data_dir" /></label><label>Log directory<input v-model="deployForm.log_dir" /></label><label>Config directory<input v-model="deployForm.config_dir" /></label></div>
          <div class="row"><label>AOF<select v-model="deployForm.appendonly"><option :value="true">Enabled</option><option :value="false">Disabled</option></select></label><label>Max memory<input v-model="deployForm.maxmemory" placeholder="Optional, e.g. 8gb" /></label><label>Eviction policy<select v-model="deployForm.maxmemory_policy"><option>noeviction</option><option>allkeys-lru</option><option>volatile-lru</option><option>allkeys-lfu</option></select></label></div>
          <label>Custom redis.conf (optional)<textarea v-model="deployForm.custom_redis_conf" rows="10" placeholder="port 6379&#10;cluster-enabled yes&#10;..."></textarea></label><button @click="createDeployment" :disabled="!isOperator">Submit deployment task</button>
        </section>

        <section v-if="active === 'tasks'" class="panel"><h2>Tasks and audit logs</h2><table><thead><tr><th>Task</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead><tbody><tr v-for="task in deployments" :key="task.id"><td><b>{{ task.name }}</b><small>{{ task.id }}</small></td><td><span class="status" :class="task.status">{{ task.status }}</span></td><td>{{ new Date(task.created_at).toLocaleString() }}</td><td><button class="link" @click="showLogs(task)">Logs</button><button v-if="['failed', 'rolled_back'].includes(task.status) && isOperator" class="link" @click="retry(task)">Retry</button></td></tr><tr v-if="!deployments.length"><td colspan="4" class="muted">No deployment tasks yet.</td></tr></tbody></table><div v-if="selectedDeployment" class="logs"><div><h3>{{ selectedDeployment.name }} logs</h3><button class="link" @click="showLogs(selectedDeployment)">Refresh</button></div><pre v-for="line in logs" :key="line.id">[{{ new Date(line.created_at).toLocaleTimeString() }}] {{ line.level }} {{ line.message }}</pre></div></section>

        <section v-if="active === 'admin'" class="two-col"><form class="panel form" @submit.prevent="createTenant"><h2>Create tenant</h2><label>Tenant name<input v-model="tenantForm.name" required /></label><button>Create tenant</button></form><form class="panel form" @submit.prevent="createUser"><h2>Create local user</h2><label>Username<input v-model="userForm.username" required /></label><label>Password<input v-model="userForm.password" type="password" required /></label><label>Tenant ID<input v-model="userForm.tenant_id" required /></label><label>Role<select v-model="userForm.role"><option value="tenant_admin">tenant_admin</option><option value="operator">operator</option><option value="auditor">auditor</option></select></label><button>Create user</button></form></section>
      </section>
    </main>
  `,
}).mount('#app')
