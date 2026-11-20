<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">数据库脚本</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button type="primary" :icon="Plus" @click="openCreate">新建脚本</el-button>
        <el-button :icon="Refresh" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="db-script-tabs">
      <el-tab-pane label="关系型数据库" name="relational" />
      <el-tab-pane label="非关系型数据库" name="nonrelational" />
    </el-tabs>

    <!-- 非关系型：占位 -->
    <div v-if="activeTab === 'nonrelational'" class="db-script-placeholder">
      <el-empty description="功能开发中，敬请期待......" />
    </div>

    <!-- 关系型：SQL 脚本库 -->
    <div v-else class="panel panel-pad">
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetcher"
        :search-fields="searchFields"
        :max-height="440"
        selectable
        sync-query
        query-prefix="sql_"
      >
        <template #batch="{ rows }">
          <el-button type="danger" size="plain" @click="batchRemove(rows)">批量删除</el-button>
        </template>
        <template #name="{ row }">
          <div class="db-name">
            <span class="db-name__title">{{ row.name }}</span>
            <span class="muted text-mono">{{ row.scriptNo }}</span>
            <el-tag v-if="row.alias" size="small" type="info" effect="plain">{{ row.alias }}</el-tag>
          </div>
        </template>
        <template #referenced="{ row }">
          <ReferenceRefs :refs="row.refs" />
        </template>
        <template #status="{ row }">
          <el-tag :type="row.status === 'ready' ? 'success' : 'info'" size="small" effect="plain">
            {{ row.status === 'ready' ? '启用' : '停用' }}
          </el-tag>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button>
          <el-button link type="warning" @click.stop="copy(row)">复制</el-button>
          <el-button link type="success" :loading="running" :disabled="running" @click.stop="run(row)">执行</el-button>
          <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
        </template>
      </BaseProTable>
    </div>

    <!-- 新建 / 编辑 -->
    <el-dialog
      v-model="editorVisible"
      :title="editingId ? '编辑数据库脚本' : '新建数据库脚本'"
      width="880px"
      top="5vh"
    >
      <el-form label-width="110px">
        <el-form-item label="脚本类型">
          <el-radio-group v-model="form.scriptType">
            <el-radio value="relational">关系型数据库脚本</el-radio>
            <el-radio value="nonrelational">非关系型数据库脚本</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-empty
          v-if="form.scriptType === 'nonrelational'"
          description="功能开发中，敬请期待......"
        />
        <template v-else>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="脚本名称" required>
              <el-input v-model="form.name" placeholder="如：造数-订单初始化" maxlength="200" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="别称">
              <el-input v-model="form.alias" placeholder="方便其他页面/场景引用，如 orders-init" maxlength="100" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="绑定执行环境">
              <el-select v-model="form.envId" clearable filterable placeholder="选择执行环境（DB 连接在此维护）" style="width: 100%">
                <el-option
                  v-for="e in envOptions"
                  :key="e.id"
                  :label="e.name"
                  :value="e.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="状态">
              <el-radio-group v-model="form.status">
                <el-radio value="ready">启用</el-radio>
                <el-radio value="disabled">停用</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="SQL 正文">
          <el-input
            v-model="form.content"
            type="textarea"
            :rows="10"
            class="text-mono"
            placeholder="多条语句用分号分隔；{{var}} 引用变量；写操作（INSERT/UPDATE/DELETE/DDL）在「执行前/后」场景放开"
          />
          <p class="muted" style="font-size: 12px; margin-top: 6px">
            断言场景走只读路径；执行前预处理 / 执行后处理走写路径（支持 MySQL/PostgreSQL 协议族、达梦、SQLite；非关系库写路径开发中）。
          </p>
          <p class="env-hint">
            变量用法：SQL 里用 <code class="text-mono" v-pre>{{变量名}}</code> 引用，例：
            <code class="text-mono" v-pre>select * from orders where id = {{orderId}} and status = '{{status}}'</code>；
            在下方「变量」区逐行定义默认值（变量名 + 默认值），执行时可由调用方传值覆盖。
          </p>
        </el-form-item>
        <el-form-item label="变量">
          <div class="sql-vars">
            <div v-for="(v, i) in form.variables" :key="i" class="sql-vars__row">
              <el-input v-model="v.name" placeholder="变量名" style="width: 180px" />
              <el-input v-model="v.value" placeholder="默认值" style="flex: 1" />
              <el-button link type="danger" @click="form.variables.splice(i, 1)">移除</el-button>
            </div>
            <el-button :icon="Plus" link type="primary" @click="form.variables.push({ name: '', value: '' })">
              添加变量
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button
          v-if="form.scriptType === 'relational'"
          type="primary"
          :loading="saving"
          @click="save"
        >保存</el-button>
      </template>
    </el-dialog>

    <!-- 执行结果 -->
    <el-dialog v-model="resultVisible" title="执行结果" width="760px" top="5vh">
      <div v-if="lastResult" class="sql-result">
        <div class="sql-result__head">
          <el-tag :type="lastResult.affectedRows ? 'warning' : 'success'" effect="plain">
            {{ lastResult.affectedRows ? `写成功（${lastResult.affectedRows} 行）` : '只读成功' }}
          </el-tag>
          <span class="muted">{{ lastResult.dbTypeLabel }} · {{ lastResult.rowCount }} 行 · {{ lastResult.durationMs }} ms</span>
        </div>
        <el-table v-if="lastResult.rows.length" :data="lastResult.rows" size="small" border max-height="280" class="sql-result__table">
          <el-table-column
            v-for="col in lastResult.columns"
            :key="col"
            :prop="col"
            :label="col"
            min-width="140"
            show-overflow-tooltip
          />
        </el-table>
        <pre v-else class="sql-result__empty">（无结果行）</pre>
        <div v-if="lastResult.error" class="sql-result__err">{{ lastResult.error }}</div>
      </div>
      <template #footer>
        <el-button @click="resultVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import ReferenceRefs from '@/components/ReferenceRefs.vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import { sqlScriptApi, testEnvApi } from '@/api'
import { useAppStore } from '@/stores/app'
import type { SqlScript, SqlScriptResult, TestEnv } from '@/api/types'

const appStore = useAppStore()
const activeTab = ref<'relational' | 'nonrelational'>('relational')
const loading = ref(false)
const tableRef = ref<TableExpose | null>(null)
const envOptions = ref<TestEnv[]>([])

const columns: ColumnDef[] = [
  { prop: 'scriptNo', label: '编号', width: 110 },
  { prop: 'name', label: '名称', minWidth: 180, slot: 'name' },
  { prop: 'alias', label: '别称', width: 130 },
  { prop: 'envName', label: '绑定环境', width: 130 },
  { prop: 'referenced', label: '是否被引用', width: 110, align: 'center', slot: 'referenced' },
  { prop: 'status', label: '状态', width: 80, slot: 'status' },
  { prop: 'actions', label: '操作', width: 230, slot: 'actions' },
]

const searchFields: SearchField[] = [
  { prop: 'keyword', label: '名称/编号/别称', type: 'text' },
  {
    prop: 'referenced', label: '是否被引用', type: 'select',
    options: [
      { label: '全部', value: '' },
      { label: '已被引用', value: 'true' },
      { label: '未被引用', value: 'false' },
    ],
  },
  {
    prop: 'status', label: '状态', type: 'select',
    options: [
      { label: '全部', value: '' },
      { label: '启用', value: 'ready' },
      { label: '停用', value: 'disabled' },
    ],
  },
]

const fetcher = async (params: Record<string, unknown>) => {
  const query: Record<string, unknown> = {
    pageNum: Number(params.pageNum ?? 1),
    pageSize: Number(params.pageSize ?? 20),
  }
  if (appStore.currentProjectId) query.projectId = appStore.currentProjectId
  const keyword = String(params.keyword ?? '').trim()
  if (keyword) query.keyword = keyword
  const refFilter = String(params.referenced ?? '').trim()
  if (refFilter === 'true' || refFilter === 'false') query.referenced = refFilter
  const status = String(params.status ?? '').trim()
  if (status) query.status = status
  return await sqlScriptApi.list(query)
}

async function reload(): Promise<void> {
  loading.value = true
  // 先刷列表，环境下拉随后补齐，保存/删除后表格能更快看到结果
  tableRef.value?.load()
  try {
    await loadEnvs()
  } finally {
    loading.value = false
  }
}

async function loadEnvs(): Promise<void> {
  const payload = await testEnvApi.list()
  envOptions.value = payload.list ?? []
}

// ---- 编辑 ----
const editorVisible = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)
const form = reactive({
  scriptType: 'relational' as 'relational' | 'nonrelational',
  name: '',
  alias: '',
  envId: null as number | null,
  status: 'ready',
  content: '',
  variables: [] as { name: string; value: string }[],
  description: '',
})

function openCreate(): void {
  editingId.value = null
  form.scriptType = activeTab.value
  form.name = ''
  form.alias = ''
  form.envId = null
  form.status = 'ready'
  form.content = ''
  form.variables = []
  form.description = ''
  editorVisible.value = true
}

async function openEdit(row: SqlScript): Promise<void> {
  try {
    const detail = await sqlScriptApi.get(row.id)
    editingId.value = detail.id
    form.scriptType = 'relational'
    form.name = detail.name
    form.alias = detail.alias
    form.envId = detail.envId ?? null
    form.status = detail.status
    form.content = detail.content
    form.variables = detail.variables.map((v) => ({ name: v.name, value: v.value }))
    form.description = detail.description
    editorVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载脚本失败')
  }
}

async function save(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请填写脚本名称')
    return
  }
  const blankNameIndex = form.variables.findIndex((v) => !v.name.trim())
  if (blankNameIndex >= 0) {
    ElMessage.warning(`第 ${blankNameIndex + 1} 个变量缺少变量名（不需要可点「移除」）`)
    return
  }
  const cleanVars = form.variables.map((v) => ({ name: v.name.trim(), value: v.value }))
  const names = cleanVars.map((v) => v.name)
  if (new Set(names).size !== names.length) {
    ElMessage.warning('变量名不能重复')
    return
  }
  const body: Record<string, unknown> = {
    name: form.name.trim(),
    alias: form.alias.trim(),
    envId: form.envId,
    status: form.status,
    content: form.content,
    variables: cleanVars,
    description: form.description,
  }
  if (appStore.currentProjectId) body.projectId = appStore.currentProjectId
  saving.value = true
  try {
    if (editingId.value) {
      await sqlScriptApi.update(editingId.value, body)
      ElMessage.success('已保存')
    } else {
      await sqlScriptApi.create(body)
      ElMessage.success('已创建')
    }
    editorVisible.value = false
    await reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row: SqlScript): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除数据库脚本「${row.name}」（${row.scriptNo}）？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    const res = await sqlScriptApi.remove(row.id)
    if (res.refs?.length) {
      ElMessage.warning(`已删除，但 ${res.refs.length} 处引用需要更新：${res.refs.map((r) => r.name).join('、')}`)
    } else {
      ElMessage.success('已删除')
    }
    await reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

const copying = ref(false)

async function copy(row: SqlScript): Promise<void> {
  if (copying.value) return
  copying.value = true
  try {
    const copyRow = await sqlScriptApi.copy(row.id)
    ElMessage.success(`已复制：${copyRow.scriptNo}（${copyRow.name}）`)
    await reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '复制失败')
  } finally {
    copying.value = false
  }
}

async function batchRemove(rows: SqlScript[]): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除 ${rows.length} 条数据库脚本？`, '批量删除', { type: 'warning' })
  } catch {
    return
  }
  try {
    for (const row of rows) {
      await sqlScriptApi.remove(row.id)
    }
    ElMessage.success(`已删除 ${rows.length} 条`)
    await reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量删除失败')
  }
}

// ---- 执行 ----
const resultVisible = ref(false)
const lastResult = ref<SqlScriptResult | null>(null)
const running = ref(false)

async function run(row: SqlScript): Promise<void> {
  if (running.value) {
    return
  }
  if (!row.envId) {
    ElMessage.warning('脚本未绑定执行环境，无法执行')
    return
  }
  running.value = true
  try {
    const res = await sqlScriptApi.execute(row.id, { allowWrite: false })
    lastResult.value = res
    resultVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '执行失败')
  } finally {
    running.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.db-script-tabs {
  margin-top: 12px;
}
.db-script-placeholder {
  padding: 40px 0;
}
.db-script-placeholder p {
  margin-top: 8px;
}
.sql-vars {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.sql-vars__row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.sql-result__head {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
}
.sql-result__table {
  width: 100%;
}
.sql-result__empty {
  color: var(--el-text-color-secondary);
  margin: 0;
}
.sql-result__err {
  margin-top: 10px;
  color: var(--el-color-danger);
  white-space: pre-wrap;
  word-break: break-all;
}
.env-hint {
  width: 100%;
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
