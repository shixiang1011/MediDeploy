<script>
import axios from 'axios'

const api = axios.create({ baseURL: '/api/' })
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('spmp-token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

const emptyHost = () => ({
  name: '',
  address: '',
  ssh_port: 22,
  ssh_user: 'root',
  ssh_password: '',
  use_sudo: false,
  sudo_password: '',
})

const emptyPackage = () => ({
  component: 'redis',
  version: '6.2.17',
  package_type: 'source',
  architecture: 'x86_64',
  description: '',
  file: null,
})

export default {
  data() {
    return {
      token: localStorage.getItem('spmp-token') || '',
      user: null,
      active: 'dashboard',
      error: '',
      notice: '',
      busy: false,
      loginForm: { username: 'admin', password: '' },
      components: [],
      hosts: [],
      packages: [],
      deployments: [],
      hostQuery: '',
      packageQuery: '',
      deploymentQuery: '',
      auditLogs: [],
      hostForm: emptyHost(),
      hostTest: null,
      editingHostId: null,
      packageForm: emptyPackage(),
      wizardOpen: false,
      wizardStep: 1,
      wizard: {
        component: 'redis',
        name: '',
        package_id: '',
        mode: 'standalone',
        primary_count: 3,
        replicas_per_primary: 1,
        es_node_count: 3,
        instances: [],
        redis_password: '',
        elastic_password: '',
        cluster_name: 'cluster-es',
        security_enabled: true,
        http_cors_enabled: true,
        heap_size: '',
        disk_watermark_low: '90%',
        disk_watermark_high: '95%',
        disk_watermark_flood_stage: '98%',
        maxmemory: '',
        maxmemory_policy: 'noeviction',
        appendonly: true,
      },
      executionTask: null,
      executionLogs: [],
      executionLastLogId: 0,
      executionTimer: null,
      executionPolling: false,
      executionNotifyOnFinish: false,
      completionDialog: false,
      catalogTimer: null,
    }
  },
  computed: {
    isOperator() {
      return this.user && ['super_admin', 'tenant_admin', 'operator'].includes(this.user.role)
    },
    canUploadPackage() {
      return this.user && ['super_admin', 'tenant_admin'].includes(this.user.role)
    },
    availableRedisPackages() {
      return this.packages.filter((item) => item.component === 'redis')
    },
    availableWizardPackages() {
      return this.packages.filter((item) => item.component === this.wizard.component)
    },
    isRedisWizard() {
      return this.wizard.component === 'redis'
    },
    isElasticsearchWizard() {
      return this.wizard.component === 'elasticsearch'
    },
    expectedInstanceCount() {
      if (this.isElasticsearchWizard) {
        return this.wizard.mode === 'standalone' ? 1 : Number(this.wizard.es_node_count)
      }
      if (this.wizard.mode === 'standalone') return 1
      return Number(this.wizard.primary_count) * (Number(this.wizard.replicas_per_primary) + 1)
    },
    selectedPackage() {
      return this.packages.find((item) => item.id === this.wizard.package_id)
    },
  },
  methods: {
    async request(method, url, body, config = {}) {
      try {
        const response = await api.request({ method, url, data: body, ...config })
        return response.data
      } catch (error) {
        if (error.response?.status === 413) {
          this.error = '上传文件超过网关允许的大小，请检查平台或上游代理的上传限制。'
        } else {
          this.error = error.response?.data?.detail || error.message || '请求失败'
        }
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
      this.clearExecutionTimer()
      localStorage.removeItem('spmp-token')
      this.token = ''
      this.user = null
    },
    async refresh() {
      if (!this.token) return
      try {
        this.user = await this.request('get', 'me')
        const [components, hosts, packages, deployments] = await Promise.all([
          this.request('get', 'components'),
          this.request('get', `hosts?q=${encodeURIComponent(this.hostQuery)}`),
          this.request('get', `packages?q=${encodeURIComponent(this.packageQuery)}`),
          this.request('get', `deployments?q=${encodeURIComponent(this.deploymentQuery)}`),
        ])
        this.components = components
        this.hosts = hosts
        this.packages = packages
        this.deployments = deployments
      } catch (_) {
        this.logout()
      }
    },
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
        draft: '尚未开始',
        queued: '等待执行',
        running: '执行中',
        succeeded: '成功',
        failed: '失败',
        rollback_queued: '等待回滚',
        rolling_back: '回滚中',
        rolled_back: '已回滚',
        cancelled: '已取消',
      }[status] || status
    },
    componentName(id) {
      return this.components.find((item) => item.id === id)?.name || id
    },
    hostName(id) {
      const host = this.hosts.find((item) => item.id === id)
      return host ? `${host.name}（${host.address}）` : id
    },
    packageTypeLabel(type) {
      return type === 'source' ? '源码包' : '已编译二进制包'
    },
    canDeletePackage(pkg) {
      return this.user?.role === 'super_admin'
        || (this.user?.role === 'tenant_admin' && !pkg.is_global)
    },
    async testHost() {
      this.error = ''
      this.notice = ''
      this.hostTest = null
      this.busy = true
      try {
        const result = await this.request('post', 'hosts/test', this.hostForm)
        this.hostTest = result
        this.notice = result.message
      } catch (_) {
        this.hostTest = { success: false }
      } finally {
        this.busy = false
      }
    },
    async createHost() {
      this.error = ''
      this.busy = true
      try {
        const item = this.editingHostId
          ? await this.request('put', `hosts/${this.editingHostId}`, this.hostForm)
          : await this.request('post', 'hosts', this.hostForm)
        if (this.editingHostId) {
          const index = this.hosts.findIndex((host) => host.id === this.editingHostId)
          if (index >= 0) this.hosts[index] = item
        } else {
          this.hosts.unshift(item)
        }
        this.hostForm = emptyHost()
        this.hostTest = null
        this.editingHostId = null
        this.notice = '服务器已通过全新 SSH 连接验证并保存，登录密码已加密存储。'
      } catch (_) {
      } finally {
        this.busy = false
      }
    },
    editHost(host) {
      this.editingHostId = host.id
      this.hostForm = {
        name: host.name,
        address: host.address,
        ssh_port: host.ssh_port,
        ssh_user: host.ssh_user,
        ssh_password: '',
        use_sudo: host.use_sudo,
        sudo_password: '',
      }
      this.hostTest = null
      this.error = ''
      this.notice = '请输入当前有效密码；平台不会回显已保存的密码。'
    },
    cancelEditHost() {
      this.editingHostId = null
      this.hostForm = emptyHost()
      this.hostTest = null
    },
    async searchHosts() {
      this.hosts = await this.request('get', `hosts?q=${encodeURIComponent(this.hostQuery)}`)
    },
    async deleteHost(host) {
      if (!window.confirm(`确认从服务器资产中删除“${host.name}（${host.address}）”吗？历史任务记录不会被删除。`)) return
      await this.request('delete', `hosts/${host.id}`)
      this.notice = `服务器资产“${host.name}”已删除。`
      await this.searchHosts()
    },
    choosePackageFile(event) {
      this.packageForm.file = event.target.files[0]
    },
    packageComponentChanged() {
      if (this.packageForm.component === 'elasticsearch') {
        this.packageForm.package_type = 'binary'
        if (this.packageForm.version === '6.2.17') this.packageForm.version = '8.17.0'
      }
    },
    async uploadPackage() {
      if (!this.packageForm.file) {
        this.error = '请选择 .tar.gz 或 .tgz 软件包。'
        return
      }
      const query = new URLSearchParams({
        component: this.packageForm.component,
        version: this.packageForm.version,
        package_type: this.packageForm.package_type,
        architecture: this.packageForm.architecture,
        description: this.packageForm.description,
      })
      const form = new FormData()
      form.append('file', this.packageForm.file)
      this.busy = true
      try {
        const item = await this.request('post', `packages?${query}`, form)
        this.packages.unshift(item)
        this.packageForm = emptyPackage()
        this.notice = '软件包上传成功。'
      } catch (_) {
      } finally {
        this.busy = false
      }
    },
    async searchPackages() {
      this.packages = await this.request('get', `packages?q=${encodeURIComponent(this.packageQuery)}`)
    },
    async deletePackage(pkg) {
      if (!window.confirm(`确认删除软件包“${pkg.filename}”吗？平台将删除该条记录及受控目录中的单个软件包文件。`)) return
      await this.request('delete', `packages/${pkg.id}`)
      this.notice = `软件包“${pkg.filename}”已删除。`
      await this.searchPackages()
    },
    openComponent(component) {
      if (!component.available) return
      this.wizardOpen = true
      this.wizardStep = 1
      this.wizard.component = component.id
      this.wizard.name = ''
      this.wizard.package_id = this.packages.find((pkg) => pkg.component === component.id)?.id || ''
      this.wizard.mode = 'standalone'
      this.wizard.primary_count = 3
      this.wizard.replicas_per_primary = 1
      this.wizard.es_node_count = 3
      this.wizard.redis_password = ''
      this.wizard.elastic_password = ''
      this.wizard.cluster_name = component.id === 'elasticsearch' ? 'cluster-es' : ''
      this.wizard.heap_size = ''
      this.wizard.maxmemory = ''
      this.wizard.instances = []
      this.syncTopology()
    },
    instanceTemplate(index) {
      if (this.isElasticsearchWizard) {
        const httpPort = 9200 + index
        return {
          host_id: this.hosts[index % Math.max(this.hosts.length, 1)]?.id || '',
          node_name: `node-${index + 1}`,
          http_port: httpPort,
          transport_port: 9300 + index,
          install_dir: `/opt/middleware/elasticsearch/${httpPort}`,
          data_dir: `/data/elasticsearch/${httpPort}/data`,
          log_dir: `/data/elasticsearch/${httpPort}/logs`,
          config_dir: `/etc/middleware/elasticsearch/${httpPort}`,
        }
      }
      const port = 6379 + index
      return {
        host_id: this.hosts[index % Math.max(this.hosts.length, 1)]?.id || '',
        port,
        install_dir: `/opt/middleware/redis/${port}`,
        data_dir: `/data/redis/${port}`,
        log_dir: `/var/log/middleware/redis/${port}`,
        config_dir: `/etc/middleware/redis/${port}`,
        custom_redis_conf: '',
      }
    },
    syncTopology() {
      const target = this.expectedInstanceCount
      while (this.wizard.instances.length < target) {
        this.wizard.instances.push(this.instanceTemplate(this.wizard.instances.length))
      }
      if (this.wizard.instances.length > target) this.wizard.instances.splice(target)
    },
    nextWizardStep() {
      this.error = ''
      if (this.wizardStep === 1 && (!this.wizard.name || !this.wizard.package_id)) {
        this.error = '请填写任务名称并选择软件包。'
        return
      }
      if (this.wizardStep === 2) {
        if (this.wizard.instances.some((item) => !item.host_id)) {
          this.error = `请为每个 ${this.componentName(this.wizard.component)} 节点选择服务器。`
          return
        }
        const endpoints = this.wizard.instances.flatMap((item) => this.isElasticsearchWizard
          ? [`${item.host_id}:${item.http_port}`, `${item.host_id}:${item.transport_port}`]
          : [`${item.host_id}:${item.port}`])
        if (new Set(endpoints).size !== endpoints.length) {
          this.error = '同一服务器不能重复使用相同端口。'
          return
        }
      }
      if (this.wizardStep === 3 && this.isRedisWizard && this.wizard.redis_password.length < 8) {
        this.error = 'Redis 密码至少需要 8 个字符。'
        return
      }
      if (this.wizardStep === 3 && this.isElasticsearchWizard && this.wizard.elastic_password.length < 6) {
        this.error = 'Elasticsearch elastic 密码至少需要 6 个字符。'
        return
      }
      if (this.wizardStep < 4) this.wizardStep += 1
    },
    previousWizardStep() {
      if (this.wizardStep > 1) this.wizardStep -= 1
    },
    async submitDeployment() {
      const config = this.isElasticsearchWizard ? {
        mode: this.wizard.mode,
        instances: this.wizard.instances.map((item) => ({ ...item })),
        cluster_name: this.wizard.cluster_name,
        elastic_password: this.wizard.elastic_password,
        security_enabled: this.wizard.security_enabled,
        http_cors_enabled: this.wizard.http_cors_enabled,
        heap_size: this.wizard.heap_size || null,
        disk_watermark_low: this.wizard.disk_watermark_low,
        disk_watermark_high: this.wizard.disk_watermark_high,
        disk_watermark_flood_stage: this.wizard.disk_watermark_flood_stage,
      } : {
        mode: this.wizard.mode,
        instances: this.wizard.instances.map((item) => ({
          ...item,
          custom_redis_conf: item.custom_redis_conf || null,
        })),
        primary_count: Number(this.wizard.primary_count),
        replicas_per_primary: Number(this.wizard.replicas_per_primary),
        redis_password: this.wizard.redis_password,
        maxmemory: this.wizard.maxmemory || null,
        maxmemory_policy: this.wizard.maxmemory_policy,
        appendonly: this.wizard.appendonly,
      }
      const body = {
        name: this.wizard.name,
        component: this.wizard.component,
        package_id: this.wizard.package_id,
        mode: this.wizard.mode,
        config,
      }
      this.busy = true
      try {
        const item = await this.request('post', 'deployments', body)
        this.deployments.unshift(item)
        this.wizardOpen = false
        this.active = 'tasks'
        this.notice = '部署任务已保存，请在任务列表中点击“开始部署”。'
      } catch (_) {
      } finally {
        this.busy = false
      }
    },
    isTerminal(status) {
      return ['succeeded', 'failed', 'rolled_back', 'cancelled'].includes(status)
    },
    clearExecutionTimer() {
      if (this.executionTimer) {
        window.clearInterval(this.executionTimer)
        this.executionTimer = null
      }
    },
    async startTask(task) {
      this.busy = true
      this.error = ''
      try {
        const started = await this.request('post', `deployments/${task.id}/start`)
        const index = this.deployments.findIndex((item) => item.id === task.id)
        if (index >= 0) this.deployments[index] = started
        await this.openExecution(started, true)
      } catch (_) {
      } finally {
        this.busy = false
      }
    },
    async openExecution(task, notifyOnFinish = false) {
      this.clearExecutionTimer()
      this.executionTask = task
      this.executionLogs = []
      this.executionLastLogId = 0
      this.executionNotifyOnFinish = notifyOnFinish
      this.completionDialog = false
      await this.pollExecution()
      if (this.executionTask && !this.isTerminal(this.executionTask.status)) {
        this.executionTimer = window.setInterval(() => this.pollExecution(), 1000)
      }
    },
    async pollExecution() {
      if (!this.executionTask || this.executionPolling) return
      const taskId = this.executionTask.id
      this.executionPolling = true
      try {
        const [task, logs] = await Promise.all([
          this.request('get', `deployments/${taskId}`),
          this.request('get', `deployments/${taskId}/logs?after_id=${this.executionLastLogId}`),
        ])
        if (!this.executionTask || this.executionTask.id !== taskId) return
        this.executionTask = task
        if (logs.length) {
          this.executionLogs.push(...logs)
          this.executionLastLogId = logs[logs.length - 1].id
          this.$nextTick(() => {
            const box = this.$refs.executionLog
            if (box) box.scrollTop = box.scrollHeight
          })
        }
        if (this.isTerminal(task.status)) {
          this.clearExecutionTimer()
          if (this.executionNotifyOnFinish) this.completionDialog = true
        }
      } catch (_) {
      } finally {
        this.executionPolling = false
      }
    },
    async closeExecution() {
      this.clearExecutionTimer()
      this.executionTask = null
      this.executionLogs = []
      this.completionDialog = false
      this.active = 'tasks'
      await this.refresh()
    },
    async retryTask(task) {
      const retry = await this.request('post', `deployments/${task.id}/retry`)
      this.notice = `重试任务“${retry.name}”已创建，请点击“开始部署”。`
      await this.refresh()
    },
    canRetryTask(task) {
      return task.status === 'rolled_back'
        || (task.status === 'failed' && task.rollback_result?.includes('预检失败'))
    },
    needsRollback(task) {
      return task.status === 'failed' && !task.rollback_result?.includes('预检失败')
    },
    async rollbackTask(task) {
      this.busy = true
      this.error = ''
      try {
        const queued = await this.request('post', `deployments/${task.id}/rollback`)
        const index = this.deployments.findIndex((item) => item.id === task.id)
        if (index >= 0) this.deployments[index] = queued
        await this.openExecution(queued, true)
      } catch (_) {
      } finally {
        this.busy = false
      }
    },
    async searchDeployments() {
      this.deployments = await this.request('get', `deployments?q=${encodeURIComponent(this.deploymentQuery)}`)
    },
    async refreshActiveList() {
      if (!this.token || document.hidden || this.executionTask) return
      try {
        if (this.active === 'hosts') await this.searchHosts()
        if (this.active === 'packages') await this.searchPackages()
        if (this.active === 'tasks') await this.searchDeployments()
      } catch (_) {}
    },
    async deleteTask(task) {
      if (!window.confirm(`确认从任务列表删除“${task.name}”吗？操作审计与已生成报告文件会保留。`)) return
      await this.request('delete', `deployments/${task.id}`)
      this.notice = `任务“${task.name}”已从列表删除。`
      await this.searchDeployments()
    },
    async downloadReport(task) {
      this.error = ''
      try {
        const response = await api.get(`deployments/${task.id}/report`, { responseType: 'blob' })
        const url = window.URL.createObjectURL(response.data)
        const link = document.createElement('a')
        link.href = url
        link.download = `SPMP-${task.component}-${task.id}.docx`
        document.body.appendChild(link)
        link.click()
        link.remove()
        window.URL.revokeObjectURL(url)
        this.notice = `任务“${task.name}”的 Word 交付报告已下载。`
      } catch (error) {
        let detail = ''
        if (error.response?.data instanceof Blob) {
          try {
            detail = JSON.parse(await error.response.data.text()).detail || ''
          } catch (_) {}
        }
        this.error = detail || error.response?.data?.detail || error.message || '报告下载失败'
      }
    },
    async loadAuditLogs() {
      this.active = 'audit'
      this.auditLogs = await this.request('get', 'audit-logs')
    },
  },
  mounted() {
    this.refresh()
    this.catalogTimer = window.setInterval(() => this.refreshActiveList(), 15000)
  },
  beforeUnmount() {
    this.clearExecutionTimer()
    if (this.catalogTimer) window.clearInterval(this.catalogTimer)
  },
}
</script>

<template>
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
      <small>请使用服务器 .env 文件中 SPMP_BOOTSTRAP_PASSWORD 的实际值登录。</small>
      <p v-if="error" class="error">{{ error }}</p>
    </section>
  </main>

  <main v-else-if="executionTask" class="execution-shell">
    <header class="execution-header">
      <div>
        <span class="eyebrow">部署执行中心</span>
        <h1>{{ executionTask.name }}</h1>
        <p>{{ componentName(executionTask.component) }} · {{ executionTask.mode === 'standalone' ? '单机模式' : '集群模式' }} · {{ executionTask.id }}</p>
      </div>
      <div class="execution-actions">
        <span class="status" :class="executionTask.status">{{ statusLabel(executionTask.status) }}</span>
        <button class="secondary" @click="closeExecution">返回任务列表</button>
      </div>
    </header>
    <section class="execution-body">
      <div class="execution-progress">
        <div :class="{done: executionTask.status !== 'queued'}"><span>1</span><b>任务排队</b></div>
        <div :class="{done: ['running','rolling_back','succeeded','failed','rolled_back'].includes(executionTask.status)}"><span>2</span><b>预检与部署</b></div>
        <div :class="{done: isTerminal(executionTask.status), warning: executionTask.status === 'rolling_back'}"><span>3</span><b>{{ executionTask.status === 'rolling_back' ? '自动回滚' : '执行完成' }}</b></div>
      </div>
      <div ref="executionLog" class="execution-log">
        <div v-if="!executionLogs.length" class="execution-waiting">正在等待部署日志……</div>
        <pre v-for="line in executionLogs" :key="line.id" :class="line.level.toLowerCase()"><span>[{{ new Date(line.created_at).toLocaleTimeString() }}]</span> <b>{{ line.level }}</b> {{ line.message }}</pre>
      </div>
      <p class="execution-hint">日志每秒自动更新，并始终滚动到最新内容，无需手动刷新。</p>
    </section>
    <div v-if="completionDialog" class="completion-backdrop">
      <section class="completion-card">
        <div class="completion-icon" :class="{success: executionTask.status === 'succeeded'}">{{ executionTask.status === 'succeeded' ? '✓' : '!' }}</div>
        <h2>任务执行完成</h2>
        <p v-if="executionTask.status === 'succeeded'">{{ componentName(executionTask.component) }} 已部署成功，可以关闭部署界面。</p>
        <p v-else-if="executionTask.status === 'rolled_back'">{{ componentName(executionTask.component) }} 部署失败，本次任务创建的资源已自动回滚。</p>
        <p v-else>{{ componentName(executionTask.component) }} 部署失败，请根据执行日志检查原因。</p>
        <span class="status" :class="executionTask.status">{{ statusLabel(executionTask.status) }}</span>
        <button @click="closeExecution">关闭部署界面</button>
      </section>
    </div>
  </main>

  <main v-else class="app-shell">
    <aside>
      <div class="brand"><span>SP</span><div><strong>SP MediDeploy</strong><small>中间件部署平台</small></div></div>
      <nav>
        <button :class="{active: active === 'dashboard'}" @click="active = 'dashboard'">运行概览</button>
        <button :class="{active: active === 'hosts'}" @click="active = 'hosts'">服务器资产</button>
        <button :class="{active: active === 'packages'}" @click="active = 'packages'">软件包仓库</button>
        <button :class="{active: active === 'deploy'}" @click="active = 'deploy'">部署中心</button>
        <button :class="{active: active === 'tasks'}" @click="active = 'tasks'">部署任务</button>
        <button :class="{active: active === 'audit'}" @click="loadAuditLogs">操作审计</button>
      </nav>
      <div class="profile"><b>{{ user.username }}</b><span>{{ roleLabel(user.role) }}</span><a @click="logout">退出登录</a></div>
    </aside>

    <section class="content">
      <header>
        <div><h1>SP MediDeploy</h1><p>统一管理软件包、服务器和中间件部署任务</p></div>
        <button class="secondary" @click="refresh">刷新数据</button>
      </header>
      <p v-if="notice" class="notice">{{ notice }}</p>
      <p v-if="error" class="error">{{ error }}</p>

      <section v-if="active === 'dashboard'" class="dashboard">
        <article><span>服务器</span><strong>{{ hosts.length }}</strong><small>已通过连接验证</small></article>
        <article><span>软件包</span><strong>{{ packages.length }}</strong><small>源码包与二进制包</small></article>
        <article><span>部署任务</span><strong>{{ deployments.length }}</strong><small>单机与集群任务</small></article>
        <article><span>可用组件</span><strong>{{ components.filter(item => item.available).length }}</strong><small>插件化持续扩展</small></article>
        <div class="panel wide"><h2>标准交付流程</h2><ol><li>登记服务器并完成 SSH、sudo、Python 3 和系统信息检查。</li><li>在通用软件包仓库上传目标中间件的源码包或二进制包。</li><li>进入部署中心，按向导确定模式、拓扑、参数和配置文件。</li><li>确认预检与回滚清单后提交任务，全程查看实时日志。</li></ol></div>
      </section>

      <section v-if="active === 'hosts'" class="two-col">
        <form class="panel form" @submit.prevent="createHost">
          <h2>{{ editingHostId ? '更新服务器凭据' : '登记服务器' }}</h2>
          <p class="muted">平台仅使用用户名和密码登录，每次保存都会禁用旧连接缓存并重新验证密码。</p>
          <label>服务器名称<input v-model="hostForm.name" required placeholder="middleware-node-01" /></label>
          <label>IP 地址或主机名<input v-model="hostForm.address" required placeholder="10.0.0.11" /></label>
          <div class="row two">
            <label>SSH 端口<input v-model.number="hostForm.ssh_port" type="number" required /></label>
            <label>SSH 用户<input v-model="hostForm.ssh_user" required /></label>
          </div>
          <label>SSH 登录密码<input v-model="hostForm.ssh_password" type="password" required /></label>
          <label class="checkbox"><input v-model="hostForm.use_sudo" type="checkbox" />使用 sudo 提权</label>
          <label v-if="hostForm.use_sudo">sudo 密码<input v-model="hostForm.sudo_password" type="password" placeholder="留空时使用 SSH 登录密码" /></label>
          <div class="action-row">
            <button type="button" class="secondary" :disabled="busy" @click="testHost">测试连接</button>
            <button v-if="editingHostId" type="button" class="secondary" :disabled="busy" @click="cancelEditHost">取消编辑</button>
            <button :disabled="busy || !isOperator">{{ editingHostId ? '验证并更新' : '验证并保存' }}</button>
          </div>
          <div v-if="hostTest?.success" class="test-result">
            <b>连接检查通过</b>
            <span>{{ hostTest.facts.os_family }} {{ hostTest.facts.os_version }}</span>
            <span>{{ hostTest.facts.architecture }} · 可用磁盘 {{ Math.round(hostTest.facts.disk_free_bytes / 1073741824) }} GB</span>
          </div>
        </form>
        <div class="panel">
          <div class="panel-toolbar">
            <div><h2>服务器资产</h2><p>仅显示当前有效资产，可按名称、地址、系统或登录用户搜索。</p></div>
            <form class="search-box" @submit.prevent="searchHosts">
              <input v-model.trim="hostQuery" placeholder="搜索服务器" aria-label="搜索服务器" />
              <button type="submit" class="secondary">搜索</button>
              <button v-if="hostQuery" type="button" class="link" @click="hostQuery = ''; searchHosts()">清空</button>
            </form>
          </div>
          <table><thead><tr><th>名称</th><th>连接地址</th><th>系统</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="host in hosts" :key="host.id"><td><b>{{ host.name }}</b><small>{{ host.ssh_user }}{{ host.use_sudo ? ' + sudo' : '' }}</small></td><td>{{ host.address }}:{{ host.ssh_port }}</td><td>{{ host.os_family }} {{ host.os_version }}<small>{{ host.architecture }}</small></td><td><span class="status succeeded">已验证</span></td><td class="table-actions"><button class="link" :disabled="busy || !isOperator" @click="editHost(host)">更新凭据</button><button class="link danger-link" :disabled="busy || !isOperator" @click="deleteHost(host)">删除</button></td></tr>
              <tr v-if="!hosts.length"><td colspan="5" class="muted">暂无服务器，请先完成连接测试并保存。</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="active === 'packages'" class="two-col">
        <form class="panel form" @submit.prevent="uploadPackage">
          <h2>上传软件包</h2>
          <p class="muted">软件包仓库面向所有中间件，仅支持 .tar.gz 和 .tgz 格式。</p>
          <label>中间件类型<select v-model="packageForm.component" @change="packageComponentChanged"><option v-for="item in components" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
          <div class="row two">
            <label>软件版本<input v-model="packageForm.version" required /></label>
            <label>软件包类型<select v-model="packageForm.package_type"><option value="source">源码包</option><option value="binary">已编译二进制包</option></select></label>
          </div>
          <label>CPU 架构<select v-model="packageForm.architecture"><option value="x86_64">x86_64</option></select></label>
          <label>说明<textarea v-model="packageForm.description" placeholder="适用系统、编译参数或其他说明"></textarea></label>
          <label>软件包<input type="file" accept=".tar.gz,.tgz" required @change="choosePackageFile" /></label>
          <button :disabled="busy || !canUploadPackage">上传软件包</button>
        </form>
        <div class="panel">
          <div class="panel-toolbar">
            <div><h2>软件包仓库</h2><p>可按中间件、版本、文件名、架构或说明搜索。</p></div>
            <form class="search-box" @submit.prevent="searchPackages">
              <input v-model.trim="packageQuery" placeholder="搜索软件包" aria-label="搜索软件包" />
              <button type="submit" class="secondary">搜索</button>
              <button v-if="packageQuery" type="button" class="link" @click="packageQuery = ''; searchPackages()">清空</button>
            </form>
          </div>
          <table><thead><tr><th>中间件</th><th>版本</th><th>类型</th><th>文件</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="pkg in packages" :key="pkg.id"><td>{{ componentName(pkg.component) }}</td><td>{{ pkg.version }}<small>{{ pkg.architecture }}</small></td><td>{{ packageTypeLabel(pkg.package_type) }}</td><td>{{ pkg.filename }}<small>{{ pkg.description }}</small></td><td><button v-if="canDeletePackage(pkg)" class="link danger-link" :disabled="busy" @click="deletePackage(pkg)">删除</button><small v-else>全局软件包只读</small></td></tr>
              <tr v-if="!packages.length"><td colspan="5" class="muted">暂无软件包。</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="active === 'deploy' && !wizardOpen">
        <div class="section-title"><div><h2>选择中间件</h2><p>选择组件后进入标准化部署向导。</p></div></div>
        <div class="component-grid">
          <article v-for="component in components" :key="component.id" :class="{disabled: !component.available}" @click="openComponent(component)">
            <div class="component-icon">{{ component.name.slice(0, 2) }}</div>
            <div><h3>{{ component.name }}</h3><p>{{ component.modes.includes('cluster') ? '支持单机与集群模式' : '支持单机模式' }}</p><span>{{ component.available ? '开始部署' : '规划中' }}</span></div>
          </article>
        </div>
      </section>

      <section v-if="active === 'deploy' && wizardOpen" class="wizard">
        <div class="wizard-head"><button class="link" @click="wizardOpen = false">返回组件列表</button><h2>部署 {{ componentName(wizard.component) }}</h2></div>
        <div class="steps">
          <div v-for="(label, index) in ['基础信息','拓扑配置','运行参数','确认提交']" :key="label" :class="{active: wizardStep === index + 1, done: wizardStep > index + 1}"><span>{{ index + 1 }}</span><b>{{ label }}</b></div>
        </div>

        <div v-if="wizardStep === 1" class="panel form wizard-panel">
          <h2>基础信息</h2>
          <label>任务名称<input v-model="wizard.name" :placeholder="`例如：生产环境 ${componentName(wizard.component)}`" /></label>
          <label>部署模式<select v-model="wizard.mode" @change="syncTopology"><option value="standalone">单机模式</option><option value="cluster">{{ isElasticsearchWizard ? 'Elasticsearch 集群' : 'Redis Cluster' }}</option></select></label>
          <label>软件包<select v-model="wizard.package_id"><option disabled value="">请选择 {{ componentName(wizard.component) }} 软件包</option><option v-for="pkg in availableWizardPackages" :key="pkg.id" :value="pkg.id">{{ componentName(pkg.component) }} {{ pkg.version }} · {{ packageTypeLabel(pkg.package_type) }} · {{ pkg.filename }}</option></select></label>
          <p v-if="!availableWizardPackages.length" class="error">软件包仓库中暂无 {{ componentName(wizard.component) }} 软件包，请先上传。</p>
        </div>

        <div v-if="wizardStep === 2" class="panel form wizard-panel">
          <h2>拓扑配置</h2>
          <div v-if="isRedisWizard && wizard.mode === 'cluster'" class="row two">
            <label>主节点数<input v-model.number="wizard.primary_count" type="number" min="3" @change="syncTopology" /></label>
            <label>每个主节点副本数<select v-model.number="wizard.replicas_per_primary" @change="syncTopology"><option :value="0">0</option><option :value="1">1（推荐）</option><option :value="2">2</option></select></label>
          </div>
          <div v-if="isElasticsearchWizard && wizard.mode === 'cluster'" class="row two">
            <label>集群节点数<input v-model.number="wizard.es_node_count" type="number" min="3" @change="syncTopology" /></label>
            <label>集群名称<input v-model="wizard.cluster_name" placeholder="cluster-es" /></label>
          </div>
          <p class="topology-tip" v-if="isRedisWizard">{{ wizard.mode === 'standalone' ? '普通 Redis 实例，共 1 个实例。' : `将创建 ${wizard.primary_count} 个主节点、每个主节点 ${wizard.replicas_per_primary} 个副本，共 ${expectedInstanceCount} 个实例。` }}</p>
          <p class="topology-tip" v-else>{{ wizard.mode === 'standalone' ? 'Elasticsearch 单机实例，共 1 个节点。' : `将创建 ${expectedInstanceCount} 个 Elasticsearch 节点，所有节点默认具备 master、data、ingest 角色。` }}</p>
          <div class="instance-list">
            <article v-for="(instance, index) in wizard.instances" :key="index">
              <h3>{{ isElasticsearchWizard ? '节点' : '实例' }} {{ index + 1 }} <span v-if="isRedisWizard && wizard.mode === 'cluster'">{{ index < wizard.primary_count ? '主节点候选' : '副本节点候选' }}</span></h3>
              <div class="row two"><label>目标服务器<select v-model="instance.host_id"><option disabled value="">请选择服务器</option><option v-for="host in hosts" :key="host.id" :value="host.id">{{ host.name }} · {{ host.address }}</option></select></label><label v-if="isRedisWizard">Redis 端口<input v-model.number="instance.port" type="number" min="1024" max="55535" /></label><label v-else>节点名称<input v-model="instance.node_name" /></label></div>
              <div v-if="isElasticsearchWizard" class="row two"><label>HTTP 端口<input v-model.number="instance.http_port" type="number" min="1024" max="55535" /></label><label>Transport 端口<input v-model.number="instance.transport_port" type="number" min="1024" max="55535" /></label></div>
              <div class="grid4"><label>安装目录<input v-model="instance.install_dir" /></label><label>数据目录<input v-model="instance.data_dir" /></label><label>日志目录<input v-model="instance.log_dir" /></label><label>配置目录<input v-model="instance.config_dir" /></label></div>
            </article>
          </div>
        </div>

        <div v-if="wizardStep === 3" class="panel form wizard-panel">
          <h2>运行参数与配置文件</h2>
          <div v-if="isRedisWizard" class="row">
            <label>Redis 密码<input v-model="wizard.redis_password" type="password" minlength="8" /></label>
            <label>最大内存<input v-model="wizard.maxmemory" placeholder="可选，例如 8gb" /></label>
            <label>内存淘汰策略<select v-model="wizard.maxmemory_policy"><option>noeviction</option><option>allkeys-lru</option><option>volatile-lru</option><option>allkeys-lfu</option></select></label>
          </div>
          <label v-if="isRedisWizard" class="checkbox"><input v-model="wizard.appendonly" type="checkbox" />开启 AOF 持久化</label>
          <div v-if="isElasticsearchWizard" class="row">
            <label>elastic 密码<input v-model="wizard.elastic_password" type="password" minlength="6" /></label>
            <label>JVM Heap<input v-model="wizard.heap_size" placeholder="可选，例如 2g" /></label>
            <label>集群名称<input v-model="wizard.cluster_name" placeholder="cluster-es" /></label>
          </div>
          <div v-if="isElasticsearchWizard" class="row">
            <label>低水位<input v-model="wizard.disk_watermark_low" /></label>
            <label>高水位<input v-model="wizard.disk_watermark_high" /></label>
            <label>只读水位<input v-model="wizard.disk_watermark_flood_stage" /></label>
          </div>
          <label v-if="isElasticsearchWizard" class="checkbox"><input v-model="wizard.security_enabled" type="checkbox" />开启 xpack.security 和 Transport TLS</label>
          <label v-if="isElasticsearchWizard" class="checkbox"><input v-model="wizard.http_cors_enabled" type="checkbox" />开启 HTTP CORS</label>
          <div v-if="isRedisWizard" class="instance-configs">
            <details v-for="(instance, index) in wizard.instances" :key="index">
              <summary>实例 {{ index + 1 }} · {{ hostName(instance.host_id) }}:{{ instance.port }} 的 redis.conf</summary>
              <p class="muted">留空时使用平台默认配置；填写后完全使用此实例的自定义配置。</p>
              <textarea v-model="instance.custom_redis_conf" rows="10" placeholder="port 6379&#10;cluster-enabled yes&#10;..."></textarea>
            </details>
          </div>
        </div>

        <div v-if="wizardStep === 4" class="panel review wizard-panel">
          <h2>确认部署配置</h2>
          <div class="review-grid">
            <div><span>中间件</span><b>{{ componentName(wizard.component) }}</b></div><div><span>部署模式</span><b>{{ wizard.mode === 'standalone' ? '单机模式' : '集群模式' }}</b></div><div><span>软件包</span><b>{{ selectedPackage?.version }} · {{ packageTypeLabel(selectedPackage?.package_type) }}</b></div><div><span>{{ isElasticsearchWizard ? '节点数量' : '实例数量' }}</span><b>{{ wizard.instances.length }}</b></div>
          </div>
          <h3>节点拓扑与路径</h3>
          <table><thead><tr><th>{{ isElasticsearchWizard ? '节点' : '实例' }}</th><th>服务器</th><th>端口</th><th>目录</th></tr></thead><tbody><tr v-for="(instance, index) in wizard.instances" :key="index"><td>{{ isElasticsearchWizard ? (instance.node_name || `node-${index + 1}`) : `实例 ${index + 1}` }}</td><td>{{ hostName(instance.host_id) }}</td><td><template v-if="isElasticsearchWizard">HTTP：{{ instance.http_port }}<small>Transport：{{ instance.transport_port }}</small></template><template v-else>{{ instance.port }}<small>Cluster Bus：{{ instance.port + 10000 }}</small></template></td><td><small>安装：{{ instance.install_dir }}</small><small>数据：{{ instance.data_dir }}</small><small>日志：{{ instance.log_dir }}</small><small>配置：{{ instance.config_dir }}</small></td></tr></tbody></table>
          <div class="review-columns">
            <div><h3>预检项目</h3><ul><li>服务器接入时已验证 SSH 密码、sudo 和 Python 3</li><li>执行前再次检查系统架构、依赖、端口和所有目录</li><li>任意实例预检失败时，不开始变更</li></ul></div>
            <div v-if="isRedisWizard"><h3>预计执行步骤</h3><ul><li>分发并解压 {{ packageTypeLabel(selectedPackage?.package_type) }}</li><li>{{ selectedPackage?.package_type === 'source' ? '在目标机编译并安装 Redis' : '校验并安装 redis-server 与 redis-cli' }}</li><li>生成配置和 systemd 服务并启动实例</li><li v-if="wizard.mode === 'cluster'">所有实例就绪后创建 Redis Cluster</li></ul></div>
            <div v-else><h3>预计执行步骤</h3><ul><li>分发并解压 Elasticsearch 二进制包</li><li>生成 elasticsearch.yml、Transport TLS 证书和 keystore</li><li>设置 vm.max_map_count 并启动 systemd 服务</li><li>设置 elastic 密码并检查集群健康状态</li></ul></div>
            <div><h3>失败回滚清单</h3><ul><li>仅停止并删除本任务创建的 systemd 服务</li><li>仅删除带任务所有权标记的精确目录</li><li>删除本任务节点专用系统用户</li><li>恢复本任务记录的 vm.max_map_count 原值</li><li>不执行 rm，不修改部署前已有业务资源</li></ul></div>
          </div>
        </div>

        <div class="wizard-actions"><button v-if="wizardStep > 1" class="secondary" @click="previousWizardStep">上一步</button><span></span><button v-if="wizardStep < 4" @click="nextWizardStep">下一步</button><button v-else :disabled="busy" @click="submitDeployment">确认并提交部署</button></div>
      </section>

      <section v-if="active === 'tasks'" class="panel">
        <div class="panel-toolbar">
          <div><h2>部署任务</h2><p>可按任务名称、任务 ID、中间件或状态搜索。</p></div>
          <form class="search-box" @submit.prevent="searchDeployments">
            <input v-model.trim="deploymentQuery" placeholder="搜索部署任务" aria-label="搜索部署任务" />
            <button type="submit" class="secondary">搜索</button>
            <button v-if="deploymentQuery" type="button" class="link" @click="deploymentQuery = ''; searchDeployments()">清空</button>
          </form>
        </div>
        <table><thead><tr><th>任务</th><th>中间件/模式</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="task in deployments" :key="task.id"><td><b>{{ task.name }}</b><small>{{ task.id }}</small></td><td>{{ componentName(task.component) }}<small>{{ task.mode === 'standalone' ? '单机模式' : '集群模式' }}</small></td><td><span class="status" :class="task.status">{{ statusLabel(task.status) }}</span></td><td>{{ new Date(task.created_at).toLocaleString() }}</td><td class="table-actions"><button v-if="task.status === 'draft' && isOperator" :disabled="busy" @click="startTask(task)">开始部署</button><button v-else class="link" @click="openExecution(task, !isTerminal(task.status))">{{ isTerminal(task.status) ? '查看记录' : '进入部署' }}</button><button v-if="task.status === 'succeeded' && isOperator" class="link report-link" @click="downloadReport(task)">下载 Word 报告</button><button v-if="needsRollback(task) && isOperator" class="link" :disabled="busy" @click="rollbackTask(task)">重新回滚</button><button v-if="canRetryTask(task) && isOperator" class="link" @click="retryTask(task)">创建重试任务</button><button v-if="isTerminal(task.status) && isOperator" class="link danger-link" :disabled="busy" @click="deleteTask(task)">删除</button></td></tr>
            <tr v-if="!deployments.length"><td colspan="5" class="muted">暂无部署任务。</td></tr>
          </tbody>
        </table>
      </section>

      <section v-if="active === 'audit'" class="panel">
        <h2>操作审计</h2>
        <table><thead><tr><th>时间</th><th>操作</th><th>资源类型</th><th>资源 ID</th></tr></thead><tbody><tr v-for="item in auditLogs" :key="item.id"><td>{{ new Date(item.created_at).toLocaleString() }}</td><td>{{ item.action }}</td><td>{{ item.resource_type }}</td><td>{{ item.resource_id }}</td></tr><tr v-if="!auditLogs.length"><td colspan="4" class="muted">暂无审计记录。</td></tr></tbody></table>
      </section>
    </section>
  </main>
</template>

<style>
.status.running,.status.queued{background:#fff3dd;color:#a76700}
.status.draft{background:#e9eef8;color:#596a88}
.status.rollback_queued,.status.rolling_back{background:#fff0db;color:#9b5a00}
.execution-shell{min-height:100vh;background:#0c1425;color:#e8eefb;padding:30px 42px;display:flex;flex-direction:column}
.execution-header{margin:0 auto 24px;width:min(1320px,100%);color:#fff}
.execution-header h1{font-size:28px;margin-top:7px}
.execution-header p{color:#91a1bd}
.eyebrow{color:#7ca2ff;font-size:12px;font-weight:800;letter-spacing:.12em}
.execution-actions{display:flex;align-items:center;gap:12px}
.execution-actions .secondary{background:#1e2b44;color:#dce6fb}
.execution-body{width:min(1320px,100%);margin:0 auto;display:flex;flex:1;min-height:0;flex-direction:column}
.execution-progress{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px}
.execution-progress div{display:flex;align-items:center;gap:9px;background:#141f34;border:1px solid #26344f;border-radius:10px;padding:11px 14px;color:#8292ae}
.execution-progress span{width:25px;height:25px;border-radius:50%;background:#283650;display:grid;place-items:center;font-size:12px}
.execution-progress .done{color:#e7edfa;border-color:#385fae}
.execution-progress .done span{background:#356bd8;color:#fff}
.execution-progress .warning{border-color:#9a6829}
.execution-log{background:#080d18;border:1px solid #26344f;border-radius:13px;padding:16px 18px;flex:1;min-height:440px;max-height:calc(100vh - 245px);overflow:auto;box-shadow:inset 0 1px 0 #ffffff08}
.execution-log pre{font-family:"Cascadia Mono",Consolas,monospace;white-space:pre-wrap;word-break:break-word;color:#c6d0e3;font-size:12px;line-height:1.65;margin:0}
.execution-log pre span{color:#60708e}
.execution-log pre b{color:#7ea7ff}
.execution-log pre.warn b{color:#ffc66d}
.execution-log pre.error{color:#ffaaaa}
.execution-log pre.error b{color:#ff6f6f}
.execution-waiting{color:#7585a2;padding:10px;font-size:13px}
.execution-hint{text-align:right;color:#687a98;font-size:12px}
.completion-backdrop{position:fixed;inset:0;background:#050914c9;display:grid;place-items:center;z-index:50;backdrop-filter:blur(5px)}
.completion-card{width:min(430px,calc(100vw - 36px));background:#fff;color:#172033;border-radius:18px;text-align:center;padding:34px;box-shadow:0 24px 80px #0008}
.completion-card p{color:#66748b;line-height:1.7}
.completion-card .status{display:inline-block;margin:3px 0 22px}
.completion-card button{display:block;width:100%}
.completion-icon{width:54px;height:54px;border-radius:50%;display:grid;place-items:center;margin:0 auto 18px;background:#ffeded;color:#c33;font-size:28px;font-weight:800}
.completion-icon.success{background:#e2f7e9;color:#168046}
.panel-toolbar{display:flex;align-items:flex-start;justify-content:space-between;gap:18px}
.panel-toolbar p{margin:7px 0 0;color:#77849b;font-size:12px}
.search-box{display:flex;align-items:center;gap:7px;min-width:310px}
.search-box input{min-width:180px}
.search-box button{white-space:nowrap}
.table-actions{min-width:185px}
.danger-link{color:#c53232;background:#fff0f0}
.danger-link:hover{color:#fff;background:#c53232}
.report-link{color:#19693b;background:#e8f6ed}
.report-link:hover{color:#fff;background:#237b49}
@media(max-width:1000px){.execution-shell{padding:20px}.execution-header{align-items:flex-start;gap:15px}.execution-progress{grid-template-columns:1fr}.execution-log{max-height:none}.panel-toolbar{flex-direction:column}.search-box{min-width:0;width:100%;flex-wrap:wrap}.search-box input{flex:1}.table-actions{min-width:150px}}
</style>
