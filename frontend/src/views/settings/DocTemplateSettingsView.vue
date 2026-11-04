<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">测试文档模板设置</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Back" @click="router.push('/settings')">返回设置</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新增模板</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="tpl-hint"
      title="模板按类别区分，AI 能力只能选用同类模板：用例生成 / XMind 转用例 / 接口测试设计用「测试用例」，报告生成用「测试报告」，测试点脑图用「测试点脑图」。"
    />

    <div class="panel panel-pad">
      <div class="toolbar">
        <el-radio-group v-model="filterKind" size="small" @change="reload">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button v-for="item in KIND_OPTIONS" :key="item.value" :value="item.value">
            {{ item.label }}
          </el-radio-button>
        </el-radio-group>
        <el-input
          v-model="keyword"
          size="small"
          class="tpl-search"
          placeholder="搜索模板名称 / 说明"
          clearable
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-button size="small" :icon="Search" @click="reload">查询</el-button>
        <div class="spacer" />
        <el-button size="small" :icon="Refresh" :loading="loading" @click="reload">刷新</el-button>
      </div>

      <el-table v-loading="loading" :data="rows" size="small" border>
        <el-table-column prop="name" label="模板名称" min-width="180">
          <template #default="{ row }">
            <span>{{ row.name }}</span>
            <el-tag v-if="row.isDefault" size="small" type="success" effect="plain" class="tpl-tag">
              默认
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="类别" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="kindTagType(row.kind)">
              {{ row.kindLabel }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="说明" min-width="180" show-overflow-tooltip />
        <el-table-column label="模板文件" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.fileName" class="text-mono">{{ row.fileName }}</span>
            <span v-else class="muted">手工填写</span>
          </template>
        </el-table-column>
        <el-table-column label="版式" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.styleHint">{{ row.styleHint }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="导出列" width="90" align="right">
          <template #default="{ row }">
            {{ row.kind === 'case' ? row.columns.length : '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="updatedAt" label="更新时间" width="170" show-overflow-tooltip />
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="removeTemplate(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && !rows.length" description="还没有同类模板，先新增一个" :image-size="80" />

      <el-pagination
        v-if="total > pageSize"
        class="tpl-pager"
        layout="total, prev, pager, next"
        :total="total"
        :page-size="pageSize"
        :current-page="pageNum"
        @current-change="onPageChange"
      />
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑模板' : '新增模板'"
      width="760px"
      @closed="resetForm"
    >
      <el-form label-width="110px">
        <el-form-item label="模板名称" required>
          <el-input v-model="form.name" maxlength="100" placeholder="例如：标准测试用例模板" />
        </el-form-item>
        <el-form-item label="模板类别" required>
          <el-select v-model="form.kind" :disabled="Boolean(editingId)">
            <el-option
              v-for="item in KIND_OPTIONS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <div class="muted tpl-form-hint">{{ kindHint }}</div>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" maxlength="500" placeholder="这份模板约束什么" />
        </el-form-item>

        <el-form-item label="模板文件">
          <div class="tpl-upload">
            <el-upload
              :show-file-list="false"
              :http-request="uploadTemplate"
              :accept="UPLOAD_ACCEPT"
            >
              <el-button :icon="Upload" :loading="uploading">上传模板文件</el-button>
            </el-upload>
            <span class="muted tpl-form-hint">
              支持 docx / xlsx / csv / md / txt / json，≤5MB；上传后自动提取正文、列名与字体字号
            </span>
          </div>
          <div v-if="form.fileName" class="tpl-file">
            <span class="text-mono">{{ form.fileName }}</span>
            <span class="muted">{{ formatSize(form.fileSize) }}</span>
            <el-button link type="danger" @click="clearFile">清除</el-button>
          </div>
          <div v-if="form.styleHint" class="muted tpl-form-hint">解析出版式：{{ form.styleHint }}</div>
        </el-form-item>

        <el-form-item v-if="form.kind === 'case'" label="导出列">
          <el-input
            v-model="columnsText"
            placeholder="用逗号分隔，例如：用例编号,用例标题,用例类型,优先级,前置条件,测试步骤,预期结果"
          />
          <div class="muted tpl-form-hint">
            导出 Excel 时按这个顺序排列表头；留空则用默认列。上传 xlsx 模板会自动带出表头。
          </div>
        </el-form-item>

        <el-form-item label="模板正文">
          <el-input
            v-model="form.content"
            type="textarea"
            :rows="9"
            maxlength="8000"
            show-word-limit
            placeholder="描述输出结构、字段要求或章节顺序；会渲染进提示词的 outputTemplate 变量"
          />
        </el-form-item>

        <el-form-item label="设为默认">
          <el-switch v-model="form.isDefault" />
          <span class="muted tpl-form-hint">同一类别只保留一个默认模板</span>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { Back, Plus, Refresh, Search, Upload } from '@element-plus/icons-vue'
import { templateApi } from '@/api'
import type { DocTemplate, DocTemplateKind } from '@/api/types'

const router = useRouter()

const KIND_OPTIONS: Array<{ label: string; value: DocTemplateKind }> = [
  { label: '测试用例', value: 'case' },
  { label: '测试报告', value: 'report' },
  { label: '测试点脑图', value: 'mindmap' },
]

const KIND_HINT: Record<DocTemplateKind, string> = {
  case: '用例生成、XMind 转用例、接口测试设计可选用；导出 Excel 时按「导出列」排列表头',
  report: '报告生成可选用；约束报告的章节与表格结构',
  mindmap: '测试点脑图可选用；约束脑图的层级与节点字段',
}

const UPLOAD_ACCEPT = '.docx,.xlsx,.csv,.md,.markdown,.txt,.json'

const filterKind = ref<DocTemplateKind | ''>('')
const keyword = ref('')
const loading = ref(false)
const rows = ref<DocTemplate[]>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = 20

function kindTagType(kind: DocTemplateKind): 'primary' | 'success' | 'warning' {
  if (kind === 'report') return 'success'
  if (kind === 'mindmap') return 'warning'
  return 'primary'
}

function formatSize(bytes: number): string {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const result = await templateApi.list({
      kind: filterKind.value || undefined,
      keyword: keyword.value.trim() || undefined,
      pageNum: pageNum.value,
      pageSize,
    })
    rows.value = result.list
    total.value = result.total
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载模板失败')
  } finally {
    loading.value = false
  }
}

function reload(): void {
  pageNum.value = 1
  void load()
}

function onPageChange(page: number): void {
  pageNum.value = page
  void load()
}

/* ---- 新增 / 编辑 ---- */

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)
const uploading = ref(false)
const columnsText = ref('')

const form = reactive({
  name: '',
  kind: 'case' as DocTemplateKind,
  description: '',
  content: '',
  styleHint: '',
  fileName: '',
  fileType: '',
  fileSize: 0,
  isDefault: false,
})

const kindHint = computed(() => KIND_HINT[form.kind])

function resetForm(): void {
  editingId.value = null
  columnsText.value = ''
  Object.assign(form, {
    name: '',
    kind: 'case' as DocTemplateKind,
    description: '',
    content: '',
    styleHint: '',
    fileName: '',
    fileType: '',
    fileSize: 0,
    isDefault: false,
  })
}

function openCreate(): void {
  resetForm()
  if (filterKind.value) {
    form.kind = filterKind.value
  }
  dialogVisible.value = true
}

function openEdit(row: DocTemplate): void {
  editingId.value = row.id
  Object.assign(form, {
    name: row.name,
    kind: row.kind,
    description: row.description,
    content: row.content,
    styleHint: row.styleHint,
    fileName: row.fileName,
    fileType: row.fileType,
    fileSize: row.fileSize,
    isDefault: row.isDefault,
  })
  columnsText.value = row.columns.join(',')
  dialogVisible.value = true
}

function clearFile(): void {
  form.fileName = ''
  form.fileType = ''
  form.fileSize = 0
  form.styleHint = ''
}

async function uploadTemplate(options: UploadRequestOptions): Promise<void> {
  uploading.value = true
  try {
    const data = new FormData()
    data.append('file', options.file)
    const parsed = await templateApi.upload(data)
    form.fileName = parsed.fileName
    form.fileType = parsed.fileType
    form.fileSize = parsed.fileSize
    form.styleHint = parsed.styleHint
    form.content = parsed.content
    if (form.kind === 'case' && parsed.columns.length) {
      columnsText.value = parsed.columns.join(',')
    }
    if (!form.name.trim()) {
      form.name = parsed.fileName.replace(/\.[^.]+$/, '')
    }
    ElMessage.success(`已解析《${parsed.fileName}》`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '模板文件解析失败')
  } finally {
    uploading.value = false
  }
}

async function save(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请填写模板名称')
    return
  }
  if (!form.content.trim()) {
    ElMessage.warning('请填写模板正文，或上传模板文件自动提取')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      kind: form.kind,
      description: form.description.trim(),
      content: form.content.trim(),
      styleHint: form.styleHint,
      fileName: form.fileName,
      fileType: form.fileType,
      fileSize: form.fileSize,
      columns: columnsText.value
        .split(/[,，]/)
        .map((item) => item.trim())
        .filter(Boolean),
      isDefault: form.isDefault,
    }
    if (editingId.value) {
      await templateApi.update(editingId.value, payload)
      ElMessage.success('模板已更新')
    } else {
      await templateApi.create(payload)
      ElMessage.success('模板已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeTemplate(row: DocTemplate): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除模板「${row.name}」？已启用的能力会回到不使用模板的状态。`, '删除模板', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await templateApi.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

onMounted(() => {
  void load()
})
</script>

<style scoped>
.tpl-hint {
  margin-bottom: 12px;
}

.tpl-search {
  width: 240px;
}

.tpl-tag {
  margin-left: 6px;
}

.tpl-pager {
  margin-top: 12px;
  justify-content: flex-end;
}

.tpl-form-hint {
  font-size: 12px;
  line-height: 1.6;
  margin-left: 8px;
}

.tpl-upload {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  width: 100%;
}

.tpl-file {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
  font-size: 12px;
}
</style>
