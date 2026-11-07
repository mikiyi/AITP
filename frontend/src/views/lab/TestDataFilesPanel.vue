<template>
  <div class="tdf">
    <div class="panel panel-pad">
      <div class="tdf-head">
        <h3 class="tdf-title">测试文件生成</h3>
        <span class="tdf-local">全程在本机生成，不联网、不经过大模型</span>
      </div>

      <el-form label-width="120px" label-position="right">
        <el-form-item label="项目">
          <span class="tdf-project">{{ projectName || '请先在本页「项目」下拉选择项目' }}</span>
          <span class="tdf-tip">批次与文件都归到该项目下，便于按项目清理</span>
        </el-form-item>

        <el-form-item label="功能点">
          <el-select v-model="moduleId" clearable filterable placeholder="选择功能点（可选）">
            <el-option
              v-for="item in labStore.moduleOptions"
              :key="item.id"
              :label="item.path"
              :value="item.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="文件格式" required>
          <el-select
            v-model="form.ext"
            filterable
            clearable
            placeholder="输入扩展名或中文名模糊匹配，如 csv / 图片 / pdf"
            :loading="catalogLoading"
          >
            <el-option-group
              v-for="group in groupedFormats"
              :key="group.name"
              :label="`${group.name}（${group.items.length}）`"
            >
              <el-option
                v-for="item in group.items"
                :key="item.ext"
                :label="item.name"
                :value="item.ext"
              >
                <span>.{{ item.ext }}</span>
                <span class="tdf-option__meta">{{ item.label }}｜最小 {{ sizeText(item.minBytes) }}</span>
              </el-option>
            </el-option-group>
          </el-select>
          <div class="tdf-under">
            共 {{ catalog.total }} 种格式可选；{{ modeSummary }}
          </div>
        </el-form-item>

        <el-form-item label="文件大小">
          <div class="tdf-size">
            <el-input v-model="form.sizeValue" type="number" min="1" placeholder="留空按最小规格" />
            <el-select v-model="form.sizeUnit" class="tdf-size__unit">
              <el-option label="字节" value="1" />
              <el-option label="KB" value="1024" />
              <el-option label="MB" value="1048576" />
            </el-select>
            <span class="tdf-tip">{{ sizeHint }}</span>
          </div>
        </el-form-item>

        <el-form-item label="文件名前缀">
          <el-input v-model="form.namePrefix" placeholder="可选，字母 / 数字 / 下划线 / 连字符" />
        </el-form-item>

        <el-form-item label="文件名后缀">
          <el-input v-model="form.nameSuffix" placeholder="可选，接在主体之后" />
        </el-form-item>

        <el-form-item label="文件名编号">
          <el-select v-model="form.tokenMode">
            <el-option
              v-for="item in TOKEN_MODE_OPTIONS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <div class="tdf-under">示例：{{ sampleName }}</div>
        </el-form-item>

        <el-form-item label="文件数量">
          <el-input v-model="form.fileCount" type="number" min="1" placeholder="默认 1" />
          <span class="tdf-tip">单批次最多 {{ MAX_FILES }} 个</span>
        </el-form-item>

        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" maxlength="200" show-word-limit
            placeholder="这批数据用来测什么，方便以后翻历史批次" />
        </el-form-item>

        <el-form-item>
          <div class="tdf-actions">
            <el-button type="primary" :icon="FolderAdd" :loading="generating" @click="generate">
              生成文件
            </el-button>
            <span class="tdf-tip">预计产出 {{ expectCount }} 个文件，约 {{ sizeText(expectBytes) }}</span>
          </div>
        </el-form-item>
      </el-form>
    </div>

    <div v-if="current" class="panel panel-pad">
      <div class="tdf-result__head">
        <h3 class="tdf-title">
          {{ current.batchNo }}
          <el-tag size="small" effect="plain">.{{ current.ext }}｜{{ current.formatLabel }}</el-tag>
          <el-tag size="small" type="info" effect="plain">{{ current.tokenLabel }}</el-tag>
        </h3>
        <div class="spacer" />
        <el-button :icon="Download" @click="open(testDataApi.archiveUrl(current.id))">
          整批下载（zip）
        </el-button>
      </div>

      <div class="tdf-meta">
        <span>{{ current.createdFiles }} 个文件</span>
        <span>共 {{ sizeText(current.totalBytes) }}</span>
        <span>目标 {{ current.sizeBytes === null ? '最小规格' : sizeText(current.sizeBytes) }} / 个</span>
        <span>目录 data/generated/{{ current.dirName }}</span>
        <span v-if="current.remark">备注：{{ current.remark }}</span>
      </div>

      <el-table :data="current.files ?? []" border stripe size="small" max-height="320">
        <el-table-column prop="fileName" label="文件名" min-width="220" show-overflow-tooltip />
        <el-table-column prop="sizeBytes" label="实际大小" width="110" align="right">
          <template #default="{ row }">{{ sizeText(row.sizeBytes) }}</template>
        </el-table-column>
        <el-table-column label="命中" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.exact ? 'success' : 'warning'" size="small" effect="plain">
              {{ row.exact ? '精确' : '近似' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="说明" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="muted">{{ row.note || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :icon="Download" @click="open(testDataApi.fileUrl(row.id))" />
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="panel panel-pad">
      <h3 class="tdf-title">历史批次</h3>
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetchBatches"
        :search-fields="[{ prop: 'keyword', label: '批次号 / 格式 / 备注' }]"
        :extra-query="{ projectId: appStore.currentProjectId ?? '' }"
        row-key="id"
        :page-size="10"
        @row-click="showBatch"
      >
        <template #format="{ row }">.{{ row.ext }}｜{{ row.formatLabel }}</template>
        <template #size="{ row }">
          {{ row.sizeBytes === null ? '最小规格' : sizeText(row.sizeBytes) }}
        </template>
        <template #total="{ row }">{{ sizeText(row.totalBytes) }}</template>
        <template #ops="{ row }">
          <el-button link type="primary" @click.stop="showBatch(row)">明细</el-button>
          <el-button link type="primary" @click.stop="open(testDataApi.archiveUrl(row.id))">下载</el-button>
          <el-button link type="danger" @click.stop="removeBatch(row)">删除</el-button>
        </template>
      </BaseProTable>
      <p class="tdf-tip">删除批次会同时删除本机目录里的文件，不只清记录。点击行可把批次明细拿到上方。</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, FolderAdd } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type TableExpose } from '@/components/BaseProTable.vue'
import { testDataApi } from '@/api'
import type { PagePayload } from '@/api/request'
import type { TestDataBatch, TestDataCatalog, TestDataFormat } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useLabStore } from '@/stores/lab'

const props = defineProps<{ moduleId?: number | null }>()

const appStore = useAppStore()
const labStore = useLabStore()

/** 与后端 TOKEN_LABELS 对齐；改这里要一起改 test_data_repo.py。 */
const TOKEN_MODE_OPTIONS = [
  { value: 'none', label: '不带编号' },
  { value: 'sequence', label: '自增数字' },
  { value: 'timestamp', label: '时间戳' },
  { value: 'timestamp_sequence', label: '时间戳 + 自增数字' },
]

const MAX_FILES = 200
const MAX_BYTES_PER_FILE = 64 * 1024 * 1024

const catalog = ref<TestDataCatalog>({ total: 0, formats: [], categories: [], modes: [] })
const catalogLoading = ref(false)
const generating = ref(false)
const tableRef = ref<TableExpose | null>(null)
const current = ref<TestDataBatch | null>(null)
const moduleId = ref<number | null>(props.moduleId ?? null)

const form = reactive({
  ext: '',
  sizeValue: '',
  sizeUnit: '1',
  namePrefix: '',
  nameSuffix: '',
  tokenMode: 'none',
  fileCount: '1',
  remark: '',
})

const projectName = computed(
  () => appStore.projects.find((item) => item.id === appStore.currentProjectId)?.name ?? '',
)

const format = computed<TestDataFormat | null>(
  () => catalog.value.formats.find((item) => item.ext === form.ext) ?? null,
)

/** 分类下拉太长，按分类分组后既能滚动定位，也让模糊匹配命中中文名。 */
const groupedFormats = computed(() => {
  const groups: Array<{ name: string; items: TestDataFormat[] }> = []
  const index = new Map<string, TestDataFormat[]>()
  catalog.value.categories.forEach((category) => {
    const items = catalog.value.formats.filter((item) => item.category === category.name)
    if (items.length) {
      index.set(category.name, items)
      groups.push({ name: category.name, items })
    }
  })
  const rest = catalog.value.formats.filter((item) => !index.has(item.category))
  if (rest.length) {
    groups.push({ name: '其他', items: rest })
  }
  return groups
})

const modeSummary = computed(() =>
  catalog.value.modes.map((item) => `${item.label} ${item.count} 种`).join('、'),
)

const sizeBytes = computed(() => {
  const raw = String(form.sizeValue).trim()
  if (!raw) {
    return null
  }
  return Math.round(Number(raw) * Number(form.sizeUnit))
})

const sizeHint = computed(() => {
  if (!format.value) {
    return '先选格式，留空则按该格式最小规格产出'
  }
  return `留空即按 ${format.value.label} 的最小规格 ${sizeText(format.value.minBytes)} 产出`
})

const expectCount = computed(() => {
  const count = Number(String(form.fileCount).trim() || 1)
  return Number.isFinite(count) && count > 0 ? Math.min(count, MAX_FILES) : 0
})

const expectBytes = computed(() => {
  const perFile = sizeBytes.value ?? format.value?.minBytes ?? 0
  return perFile * expectCount.value
})

/** 让命名规则在提交前就能看见结果，避免生成一堆名字不对的文件。 */
const sampleName = computed(() => {
  if (!format.value) {
    return '选择格式后显示示例文件名'
  }
  const stamp = '20261105103000'
  const parts: string[] = []
  if (form.namePrefix.trim()) {
    parts.push(form.namePrefix.trim())
  }
  parts.push('aitp')
  if (form.nameSuffix.trim()) {
    parts.push(form.nameSuffix.trim())
  }
  if (form.tokenMode === 'sequence') {
    parts.push('01')
  } else if (form.tokenMode === 'timestamp') {
    parts.push(stamp)
  } else if (form.tokenMode === 'timestamp_sequence') {
    parts.push(`${stamp}01`)
  }
  return `${parts.join('_')}.${format.value.ext}`
})

const columns: ColumnDef[] = [
  { prop: 'batchNo', label: '批次号', width: 150 },
  { prop: 'formatLabel', label: '格式', minWidth: 160, slot: 'format' },
  { prop: 'sizeBytes', label: '单文件大小', width: 110, slot: 'size' },
  { prop: 'createdFiles', label: '数量', width: 70, align: 'right' },
  { prop: 'totalBytes', label: '总大小', width: 100, align: 'right', slot: 'total' },
  { prop: 'tokenLabel', label: '编号', width: 110 },
  { prop: 'remark', label: '备注', minWidth: 140 },
  { prop: 'createdAt', label: '生成时间', width: 170 },
  { prop: 'ops', label: '操作', width: 150, align: 'center', fixed: 'right', slot: 'ops' },
]

function sizeText(value: number | null | undefined): string {
  const bytes = Number(value ?? 0)
  if (bytes < 1024) {
    return `${bytes} B`
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function open(url: string): void {
  window.open(url, '_blank')
}

async function loadCatalog(): Promise<void> {
  catalogLoading.value = true
  try {
    catalog.value = await testDataApi.catalog()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载文件格式目录失败')
  } finally {
    catalogLoading.value = false
  }
}

async function generate(): Promise<void> {
  if (!appStore.currentProjectId) {
    ElMessage.warning('请先在本页「项目」下拉选择项目')
    return
  }
  if (!form.ext) {
    ElMessage.warning('请选择文件格式')
    return
  }
  const bytes = sizeBytes.value
  if (bytes !== null && bytes <= 0) {
    ElMessage.warning('文件大小必须大于 0')
    return
  }
  if (bytes !== null && bytes > MAX_BYTES_PER_FILE) {
    ElMessage.warning(`单个文件不能超过 ${sizeText(MAX_BYTES_PER_FILE)}`)
    return
  }
  generating.value = true
  try {
    current.value = await testDataApi.generate({
      projectId: appStore.currentProjectId,
      moduleId: moduleId.value ?? undefined,
      ext: form.ext,
      sizeBytes: bytes ?? undefined,
      fileCount: Number(form.fileCount || 1),
      namePrefix: form.namePrefix.trim(),
      nameSuffix: form.nameSuffix.trim(),
      tokenMode: form.tokenMode,
      remark: form.remark.trim(),
    })
    ElMessage.success(`已生成 ${current.value.createdFiles} 个文件，共 ${sizeText(current.value.totalBytes)}`)
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '生成失败')
  } finally {
    generating.value = false
  }
}

/** 历史批次只带统计，明细要按批次号再取一次。 */
async function showBatch(row: TestDataBatch): Promise<void> {
  try {
    current.value = await testDataApi.batch(Number(row.id))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '读取批次明细失败')
  }
}

async function removeBatch(row: TestDataBatch): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除批次「${row.batchNo}」？本机 data/generated/${row.dirName} 下的 ${row.createdFiles} 个文件会一起删除。`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await testDataApi.removeBatch(Number(row.id))
    if (current.value && Number(current.value.id) === Number(row.id)) {
      current.value = null
    }
    ElMessage.success('批次与文件已删除')
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

function fetchBatches(query: Record<string, unknown>): Promise<PagePayload<TestDataBatch>> {
  return testDataApi.batches(query)
}

watch(
  () => props.moduleId,
  (value) => {
    moduleId.value = value ?? null
  },
)

// 项目是全局切换的，批次列表跟着换，否则会看到别的项目的批次
watch(
  () => appStore.currentProjectId,
  () => {
    current.value = null
    tableRef.value?.reload()
  },
)

onMounted(() => {
  void loadCatalog()
})
</script>

<style scoped>
.tdf {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.tdf-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.tdf-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 600;
}

.tdf-head .tdf-title {
  margin-bottom: 0;
}

.tdf-local {
  padding: 2px 8px;
  font-size: 12px;
  color: var(--el-color-success);
  border: 1px solid var(--el-color-success-light-5);
  border-radius: 4px;
  background: var(--el-color-success-light-9);
}

.tdf-result__head {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}

.tdf-result__head .tdf-title {
  margin-bottom: 0;
}

.tdf-project {
  font-weight: 600;
}

.tdf-tip {
  margin-left: 8px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.tdf-under {
  width: 100%;
  font-size: 12px;
  line-height: 18px;
  color: var(--aitp-text-weak);
}

.tdf-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin-bottom: 10px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.tdf-size {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.tdf-size .el-input {
  width: 180px;
}

.tdf-size__unit {
  width: 90px;
}

.tdf-size__unit + .tdf-tip {
  margin-left: 0;
}

.tdf-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.tdf-option__meta {
  float: right;
  margin-left: 18px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}
</style>
