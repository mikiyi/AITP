<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">项目资产包</h2>
      <div class="toolbar" style="margin: 0">
        <el-select v-model="projectId" placeholder="选择项目" style="width: 220px">
          <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
      </div>
    </div>

    <div class="panel panel-pad pkg-block">
      <div class="pkg-head">
        <h3>导出结构包</h3>
        <el-button type="primary" :icon="Download" :loading="exporting" :disabled="!projectId" @click="doExport">
          导出 .zip
        </el-button>
      </div>
      <p class="pkg-desc">
        导出当前项目的模块树、版本、需求、用例、缺陷、知识、自动化脚本与断言规则；
        不含执行历史、证据附件、密钥和附件文件。导入时 id 全部重新分配，外键自动重映射。
      </p>
    </div>

    <div class="panel panel-pad pkg-block">
      <div class="pkg-head">
        <h3>导入到本机</h3>
        <el-tag size="small" effect="plain">同编码项目默认拒绝</el-tag>
      </div>
      <el-upload
        :auto-upload="false"
        :limit="1"
        accept=".zip"
        :on-change="onFileChange"
        :on-exceed="onExceed"
      >
        <el-button :icon="Upload">选择资产包</el-button>
        <template #tip><div class="pkg-tip">选择后自动预览计数与冲突；支持改名导入</div></template>
      </el-upload>

      <template v-if="preview">
        <el-alert
          v-if="preview.conflict"
          type="warning"
          :closable="false"
          class="pkg-alert"
          :title="`项目编码「${preview.manifest.project.code}」已存在（${preview.existingProject?.name ?? ''}），请选择改名导入`"
        />
        <el-table :data="countRows" border size="small" class="pkg-counts">
          <el-table-column prop="label" label="资产" />
          <el-table-column prop="count" label="条数" width="100" align="right" />
        </el-table>
        <div class="pkg-actions">
          <el-radio-group v-model="conflict" :disabled="!preview.conflict">
            <el-radio value="rename">改名导入</el-radio>
            <el-radio value="skip">跳过（编码冲突时报错）</el-radio>
          </el-radio-group>
          <el-button type="primary" :loading="importing" @click="doImport">导入</el-button>
        </div>
        <p v-if="importResult" class="pkg-result">
          已导入为新项目「{{ importResult.code }}」（ID {{ importResult.projectId }}），
          共 {{ totalCount }} 条资产；请到项目切换器选择它。
        </p>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { Download, Upload } from '@element-plus/icons-vue'
import { projectPackageApi } from '@/api'
import type { ProjectPackageImportResult, ProjectPackagePreview } from '@/api/types'
import { useAppStore } from '@/stores/app'

const TABLE_LABELS: Record<string, string> = {
  biz_project_version: '项目版本',
  biz_module: '模块',
  biz_requirement: '需求',
  biz_test_case: '测试用例',
  biz_bug: '缺陷',
  biz_knowledge_article: '知识条目',
  biz_automation_script: '自动化脚本',
  biz_assertion_rule: '断言规则',
}

const appStore = useAppStore()
const projectId = ref<number | null>(appStore.currentProjectId ?? null)
const exporting = ref(false)
const importing = ref(false)
const file = ref<File | null>(null)
const preview = ref<ProjectPackagePreview | null>(null)
const importResult = ref<ProjectPackageImportResult | null>(null)
const conflict = ref<'rename' | 'skip'>('rename')

watch(() => appStore.currentProjectId, (value) => {
  projectId.value = value ?? null
})

const countRows = computed(() =>
  Object.entries(preview.value?.counts ?? {}).map(([table, count]) => ({
    label: TABLE_LABELS[table] ?? table,
    count,
  })),
)

const totalCount = computed(() =>
  countRows.value.reduce((sum, row) => sum + row.count, 0),
)

function onExceed(): void {
  ElMessage.warning('一次只能选择一个资产包')
}

async function onFileChange(uploadFile: UploadFile): Promise<void> {
  file.value = (uploadFile.raw as File | undefined) ?? null
  preview.value = null
  importResult.value = null
  if (!file.value) return
  const form = new FormData()
  form.append('file', file.value)
  try {
    preview.value = await projectPackageApi.preview(form)
    conflict.value = preview.value.conflict ? 'rename' : 'skip'
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '资产包预览失败')
  }
}

async function doExport(): Promise<void> {
  if (!projectId.value) return
  exporting.value = true
  try {
    const result = await projectPackageApi.exportPackage(projectId.value)
    const url = URL.createObjectURL(result.blob)
    const link = document.createElement('a')
    link.href = url
    link.download = result.filename || 'project-package.zip'
    link.click()
    URL.revokeObjectURL(url)
    ElMessage.success('资产包已导出')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '导出失败')
  } finally {
    exporting.value = false
  }
}

async function doImport(): Promise<void> {
  if (!file.value) return
  importing.value = true
  try {
    const form = new FormData()
    form.append('file', file.value)
    form.append('conflict', conflict.value)
    importResult.value = await projectPackageApi.importPackage(form)
    ElMessage.success(`已导入为「${importResult.value.code}」`)
    await appStore.loadProjects()
  } finally {
    importing.value = false
  }
}
</script>

<style scoped>
.pkg-block {
  margin-bottom: 14px;
}

.pkg-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.pkg-head h3 {
  margin: 0;
  font-size: 15px;
}

.pkg-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--aitp-text-weak);
}

.pkg-tip {
  margin-left: 8px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.pkg-alert {
  margin: 12px 0;
}

.pkg-counts {
  margin-top: 12px;
}

.pkg-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}

.pkg-result {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--el-color-success);
}
</style>
