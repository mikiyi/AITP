<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">测试环境配置</h2>
      <div class="page-header__actions">
        <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建环境</el-button>
      </div>
    </div>

    <RouteSubNav :items="EXEC_TABS" />

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>环境清单</h3>
        <span class="muted">首页环境健康监测会按这里配置的目标做本机可达性探测</span>
      </div>
      <p class="muted settings-block__hint">
        数据库口令加密存在本机系统密钥库（Windows DPAPI / macOS 钥匙串），页面只显示掩码。
        这些信息<strong>只用于本机的健康监测，不会发给任何大模型</strong>。
      </p>

      <el-table v-if="envs.length" :data="envs" border size="small">
        <el-table-column label="环境" min-width="180">
          <template #default="{ row }">
            <div class="env-name">
              {{ row.name }}
              <el-tag v-if="row.kind && row.kind !== 'test'" size="small" :type="kindTagType(row.kind)" effect="plain">
                {{ row.kindLabel || row.kind }}
              </el-tag>
              <el-tag v-if="row.isDefault" size="small" type="success" effect="plain">默认</el-tag>
            </div>
            <div v-if="row.description" class="muted env-desc">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column label="应用服务器" min-width="200">
          <template #default="{ row }">
            <div class="app-targets">
              <span>{{ row.app.servers?.[0]?.baseUrl || row.app.baseUrl || '—' }}</span>
              <el-tag v-if="(row.app.servers?.length ?? 0) > 1" size="small" effect="plain">
                共 {{ row.app.servers?.length }} 台
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="数据库" min-width="200">
          <template #default="{ row }">
            {{ dbTypeLabel(row.db.type) }} · {{ row.db.host || '—' }}{{ row.db.port ? ':' + row.db.port : '' }}
            <el-icon v-if="row.db.hasPassword" class="env-lock"><Lock /></el-icon>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="probe(row)">探测</el-button>
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="!row.isDefault" link size="small" @click="setDefault(row)">设为默认</el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="还没有测试环境，点右上角「新建环境」添加一套" :image-size="90" />
    </div>

    <div v-if="probeResult" class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>探测结果 · {{ probeTargetName }}</h3>
        <el-tag :type="probeResult.overall === 'healthy' ? 'success' : 'danger'" size="small" effect="plain">
          {{ probeResult.overall === 'healthy' ? '整体健康' : '存在异常' }}
        </el-tag>
      </div>
      <el-descriptions :column="1" border size="small" class="settings-desc">
        <el-descriptions-item label="应用服务器">
          <el-tag :type="probeResult.app.ok ? 'success' : 'danger'" size="small" effect="plain">
            {{ probeResult.app.ok ? '可达' : '不可达' }}
          </el-tag>
          {{ probeResult.app.message }}
          <div v-if="(probeResult.app.servers?.length ?? 0) > 1" class="probe-servers">
            <div v-for="(server, index) in probeResult.app.servers" :key="index" class="probe-server">
              <span class="text-mono">{{ server.baseUrl || '未配置' }}</span>
              <el-tag :type="server.ok ? 'success' : 'danger'" size="small" effect="plain">
                {{ server.ok ? '可达' : '不可达' }}
              </el-tag>
              <span class="muted">{{ server.message }}</span>
            </div>
          </div>
        </el-descriptions-item>
        <el-descriptions-item label="数据库服务器">
          <el-tag :type="probeResult.db.ok ? 'success' : 'danger'" size="small" effect="plain">
            {{ probeResult.db.ok ? '可达' : '不可达' }}
          </el-tag>
          {{ probeResult.db.message }}
        </el-descriptions-item>
      </el-descriptions>
    </div>

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑环境' : '新建环境'" width="640px" destroy-on-close>
      <el-form label-width="130px">
        <el-divider content-position="left">基本信息</el-divider>
        <el-form-item label="环境名称" required>
          <el-input v-model="form.name" placeholder="例如：测试环境、预发环境" maxlength="100" />
        </el-form-item>
        <el-form-item label="环境类型">
          <el-radio-group v-model="form.kind">
            <el-radio-button v-for="item in envKinds" :key="item.value" :value="item.value">
              {{ item.label }}
            </el-radio-button>
          </el-radio-group>
          <span class="muted form-hint">生产环境在发起轮次时会额外弹窗确认</span>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" placeholder="选填，用途备注" maxlength="500" />
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="form.isDefault" />
          <span class="muted form-hint">首页健康监测默认监测这套环境</span>
        </el-form-item>

        <el-divider content-position="left">应用服务器（可多台）</el-divider>
        <div v-for="(server, index) in form.app.servers" :key="index" class="app-server">
          <div class="app-server__head">
            <span>应用服务器 {{ index + 1 }}</span>
            <el-button
              link
              type="danger"
              size="small"
              :disabled="form.app.servers.length === 1"
              @click="removeAppServer(index)"
            >
              删除
            </el-button>
          </div>
          <el-form-item label="访问地址">
            <el-input v-model="server.baseUrl" placeholder="例如 http://192.168.1.10:8080" />
          </el-form-item>
          <el-form-item label="健康检查路径">
            <el-input v-model="server.healthPath" placeholder="例如 /actuator/health，留空则探测根路径" />
          </el-form-item>
          <el-form-item label="资源监控地址">
            <el-input v-model="server.metricsUrl" placeholder="选填，例如 http://192.168.1.10:9100/metrics" />
          </el-form-item>
        </div>
        <el-form-item>
          <el-button link type="primary" :icon="Plus" @click="addAppServer">新增一台</el-button>
          <span class="muted form-hint">网关 / 多节点 / 前后端分离时可分别登记；整体健康要求全部可达</span>
        </el-form-item>

        <el-divider content-position="left">数据库服务器</el-divider>
        <el-form-item label="数据库类型">
          <el-select v-model="form.db.type" @change="onDbTypeChange">
            <el-option v-for="t in dbTypes" :key="t" :label="dbTypeLabel(t)" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="主机">
          <el-input v-model="form.db.host" placeholder="数据库主机或 IP" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input v-model.number="form.db.port" placeholder="留空用该类型默认端口" />
        </el-form-item>
        <el-form-item label="库名">
          <el-input v-model="form.db.name" placeholder="数据库名 / 实例名" />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="form.db.username" placeholder="登录用户名" />
        </el-form-item>
        <el-form-item label="口令">
          <el-input
            v-model="form.db.password"
            type="password"
            show-password
            :placeholder="form.id && form.db.hasPassword ? `已加密保存（${form.db.passwordMasked}），留空则不变` : '加密存入本机密钥库'"
          />
        </el-form-item>
        <el-form-item label="资源监控地址">
          <el-input v-model="form.db.metricsUrl" placeholder="选填，例如 http://192.168.1.20:9100/metrics" />
          <span class="muted form-hint">远程数据库服务器填 node_exporter 的 /metrics 或 JSON 指标接口；本机地址留空即自动采集</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button :loading="probingDraft" @click="probeDraft">试连</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onActivated, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, Lock, Plus } from '@element-plus/icons-vue'
import RouteSubNav from '@/components/RouteSubNav.vue'
import { EXEC_TABS } from '@/router/subnav'
import { testEnvApi } from '@/api'
import type { TestEnv, TestEnvListResult, TestEnvProbe } from '@/api/types'

const router = useRouter()

const envs = ref<TestEnv[]>([])
const dbTypes = ref<string[]>([])
const dbTypeLabels = ref<Record<string, string>>({})
const dbDefaultPorts = ref<Record<string, number>>({})
const dialogVisible = ref(false)
const saving = ref(false)
const probingDraft = ref(false)
const probeResult = ref<TestEnvProbe | null>(null)
const probeTargetName = ref('')

function dbTypeLabel(type: string): string {
  return dbTypeLabels.value[type] ?? type
}

const envKinds = [
  { value: 'dev', label: '开发' },
  { value: 'test', label: '测试' },
  { value: 'staging', label: '预发' },
  { value: 'prod', label: '生产' },
]

function kindTagType(kind: string): 'success' | 'warning' | 'danger' | 'info' {
  if (kind === 'prod') return 'danger'
  if (kind === 'staging') return 'warning'
  if (kind === 'dev') return 'info'
  return 'success'
}

function emptyAppServer() {
  return { baseUrl: '', healthPath: '', metricsUrl: '' }
}

const emptyForm = () => ({
  id: null as number | null,
  name: '',
  description: '',
  kind: 'test',
  isDefault: false,
  app: { servers: [emptyAppServer()] },
  db: { type: 'mysql', host: '', port: null as number | null, name: '', username: '', password: '', hasPassword: false, passwordMasked: '', metricsUrl: '' },
})

const form = reactive(emptyForm())

async function load(): Promise<void> {
  try {
    const result: TestEnvListResult = await testEnvApi.list()
    envs.value = result.list
    dbTypes.value = result.dbTypes
    dbTypeLabels.value = result.dbTypeLabels ?? {}
    dbDefaultPorts.value = result.dbDefaultPorts
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载环境配置失败')
  }
}

function resetForm(): void {
  Object.assign(form, emptyForm())
}

function openCreate(): void {
  resetForm()
  probeResult.value = null
  dialogVisible.value = true
}

function openEdit(row: TestEnv): void {
  resetForm()
  form.id = row.id
  form.name = row.name
  form.description = row.description
  form.kind = row.kind || 'test'
  form.isDefault = row.isDefault
  form.app.servers = (row.app.servers?.length ? row.app.servers : [row.app]).map((server) => ({
    baseUrl: server.baseUrl ?? '',
    healthPath: server.healthPath ?? '',
    metricsUrl: server.metricsUrl ?? '',
  }))
  form.db.type = row.db.type
  form.db.host = row.db.host
  form.db.port = row.db.port
  form.db.name = row.db.name
  form.db.username = row.db.username
  form.db.hasPassword = row.db.hasPassword
  form.db.passwordMasked = row.db.passwordMasked
  form.db.metricsUrl = row.db.metricsUrl ?? ''
  probeResult.value = null
  dialogVisible.value = true
}

function onDbTypeChange(): void {
  // 切类型时自动带出该库默认端口，用户仍可手改
  const preset = dbDefaultPorts.value[form.db.type]
  if (preset) form.db.port = preset
}

function addAppServer(): void {
  form.app.servers.push(emptyAppServer())
}

function removeAppServer(index: number): void {
  if (form.app.servers.length > 1) form.app.servers.splice(index, 1)
}

function payload(): Record<string, unknown> {
  return {
    name: form.name,
    description: form.description,
    kind: form.kind,
    isDefault: form.isDefault,
    app: {
      servers: form.app.servers.map((server) => ({
        baseUrl: server.baseUrl,
        healthPath: server.healthPath,
        metricsUrl: server.metricsUrl,
      })),
    },
    db: {
      type: form.db.type,
      host: form.db.host,
      port: form.db.port,
      name: form.db.name,
      username: form.db.username,
      password: form.db.password,
      metricsUrl: form.db.metricsUrl,
    },
  }
}

async function save(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请填写环境名称')
    return
  }
  saving.value = true
  try {
    if (form.id) {
      await testEnvApi.update(form.id, payload())
      ElMessage.success('环境已更新')
    } else {
      await testEnvApi.create(payload())
      ElMessage.success('环境已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function probeDraft(): Promise<void> {
  probingDraft.value = true
  try {
    const result = await testEnvApi.probeDraft(payload())
    probeResult.value = result
    probeTargetName.value = form.name || '未命名环境'
    dialogVisible.value = false
    ElMessage({
      type: result.overall === 'healthy' ? 'success' : 'warning',
      message: result.overall === 'healthy' ? '试连通过' : '试连发现异常，详见下方结果',
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '试连失败')
  } finally {
    probingDraft.value = false
  }
}

async function probe(row: TestEnv): Promise<void> {
  try {
    const result = await testEnvApi.probe(row.id)
    probeResult.value = result
    probeTargetName.value = row.name
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '探测失败')
  }
}

async function setDefault(row: TestEnv): Promise<void> {
  try {
    await testEnvApi.update(row.id, { isDefault: true })
    ElMessage.success(`已将「${row.name}」设为默认监测环境`)
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '设置默认失败')
  }
}

async function remove(row: TestEnv): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除环境「${row.name}」吗？其加密口令会一并清除。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await testEnvApi.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

let activatedOnce = false
onMounted(() => {
  void load()
})
onActivated(() => {
  // keep-alive 首次挂载时 onActivated 也会触发，跳过以免重复请求
  if (activatedOnce) {
    void load()
  }
  activatedOnce = true
})
</script>

<style scoped>
.page-header__actions {
  display: flex;
  gap: 8px;
}

.settings-block {
  margin-bottom: 16px;
}

.settings-block__head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.settings-block__head h3 {
  margin: 0;
  font-size: 15px;
}

.settings-block__hint {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.7;
}

.env-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.env-desc {
  font-size: 12px;
  margin-top: 2px;
}

.env-lock {
  vertical-align: -2px;
  color: var(--el-color-success);
  margin-left: 4px;
}

.settings-desc {
  margin-top: 4px;
}

.form-hint {
  margin-left: 10px;
  font-size: 12px;
}

/* 多台应用服务器：每台一块，边界清楚但不抢表单主线 */
.app-server {
  width: 100%;
  margin-bottom: 10px;
  padding: 10px 12px 0;
  border: 1px solid var(--aitp-border);
  border-radius: 10px;
  background: var(--aitp-subtle);
}

.app-server__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.app-targets {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.probe-servers {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
}

.probe-server {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
</style>
