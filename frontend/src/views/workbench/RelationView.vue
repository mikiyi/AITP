<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">实体关联</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建关联</el-button>
      </div>
    </div>

    <div class="relation-layout">
      <div class="panel panel-pad">
        <h3 class="section-title">按实体查看</h3>
        <el-form label-width="86px" size="small">
          <el-form-item label="实体类型">
            <el-select v-model="queryType" placeholder="选择类型" style="width: 100%" @change="onTypeChange">
              <el-option v-for="item in entityTypes" :key="item" :label="typeLabel(item)" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="实体 ID">
            <el-input-number v-model="queryId" :min="1" controls-position="right" style="width: 100%" />
          </el-form-item>
        </el-form>
        <el-button type="primary" :icon="Search" :loading="loadingPeer" @click="loadPeer">
          查看关联
        </el-button>
        <p class="muted hint">
          关联是双向的：这条关系在来源侧和目标侧都能查到。
        </p>

        <el-table v-if="peerList.length" :data="peerList" border size="small" class="peer-table">
          <el-table-column label="方向" width="76" align="center">
            <template #default="scope">
              <el-tag size="small" effect="plain">{{ scope.row.direction === 'out' ? '出' : '入' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="关系" width="100">
            <template #default="scope">
              <el-tag size="small" type="info">{{ scope.row.relationType }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="关联实体" min-width="160">
            <template #default="scope">
              {{ typeLabel(scope.row.peerType) }} #{{ scope.row.peerId }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="76" fixed="right">
            <template #default="scope">
              <el-button link type="danger" @click="remove(scope.row.id)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else-if="!loadingPeer" description="查一个实体看看它的关系网" :image-size="70" />
      </div>

      <div class="panel panel-pad">
        <h3 class="section-title">
          全量关联
          <el-tag size="small" effect="plain">{{ total }} 条</el-tag>
        </h3>
        <div class="toolbar">
          <el-select v-model="filters.sourceType" placeholder="来源类型" clearable style="width: 140px" @change="reload">
            <el-option v-for="item in entityTypes" :key="item" :label="typeLabel(item)" :value="item" />
          </el-select>
          <el-select v-model="filters.targetType" placeholder="目标类型" clearable style="width: 140px" @change="reload">
            <el-option v-for="item in entityTypes" :key="item" :label="typeLabel(item)" :value="item" />
          </el-select>
          <el-select v-model="filters.relationType" placeholder="关系类型" clearable style="width: 140px" @change="reload">
            <el-option v-for="item in relationTypes" :key="item" :label="relationLabel(item)" :value="item" />
          </el-select>
          <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
          <el-button :icon="RefreshLeft" @click="resetFilters">重置</el-button>
        </div>

        <el-table v-loading="loading" :data="rows" border stripe>
          <el-table-column label="来源" min-width="160">
            <template #default="scope">
              {{ typeLabel(scope.row.sourceType) }} #{{ scope.row.sourceId }}
            </template>
          </el-table-column>
          <el-table-column label="关系" width="120">
            <template #default="scope">
              <el-tag size="small" type="info">{{ relationLabel(scope.row.relationType) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="目标" min-width="160">
            <template #default="scope">
              {{ typeLabel(scope.row.targetType) }} #{{ scope.row.targetId }}
            </template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="160" show-overflow-tooltip />
          <el-table-column label="创建时间" width="170">
            <template #default="{ row }">{{ dateTimeText(row.createdAt) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="scope">
              <el-button link type="danger" @click="remove(scope.row.id)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <span class="muted">暂无关联</span>
          </template>
        </el-table>

        <div class="pager">
          <el-pagination
            v-model:current-page="pageNum"
            v-model:page-size="pageSize"
            :total="total"
            :page-sizes="[20, 50, 100]"
            layout="total, sizes, prev, pager, next"
            @current-change="load"
            @size-change="reload"
          />
        </div>
      </div>
    </div>

    <el-dialog v-model="createVisible" title="新建关联" width="560px">
      <el-form label-width="96px">
        <el-form-item label="来源类型" required>
          <el-select v-model="form.sourceType" placeholder="选择类型" style="width: 100%">
            <el-option v-for="item in entityTypes" :key="item" :label="typeLabel(item)" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源 ID" required>
          <el-input-number v-model="form.sourceId" :min="1" controls-position="right" style="width: 100%" />
        </el-form-item>
        <el-form-item label="关系类型" required>
          <el-select v-model="form.relationType" placeholder="选择关系" style="width: 100%">
            <el-option
              v-for="item in relationTypes"
              :key="item"
              :label="relationLabel(item)"
              :value="item"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="目标类型" required>
          <el-select v-model="form.targetType" placeholder="选择类型" style="width: 100%">
            <el-option v-for="item in entityTypes" :key="item" :label="typeLabel(item)" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标 ID" required>
          <el-input-number v-model="form.targetId" :min="1" controls-position="right" style="width: 100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, RefreshLeft, Search } from '@element-plus/icons-vue'
import { relationApi } from '@/api'
import type { EntityRelation } from '@/api/types'
import { dateTimeText } from '@/utils/format'

const ENTITY_LABELS: Record<string, string> = {
  requirement: '需求',
  module: '功能点',
  case: '用例',
  bug: '缺陷',
  knowledge: '知识',
  report: '报告',
  regression: '回归清单',
  ai_record: '执行记录',
}

const RELATION_LABELS: Record<string, string> = {
  relates: '相关',
  verifies: '验证',
  blocks: '阻塞',
  derived_from: '派生自',
  covers: '覆盖',
  duplicates: '重复',
}

const entityTypes = ref<string[]>([])
const relationTypes = ref<string[]>([])
const rows = ref<EntityRelation[]>([])
const peerList = ref<Array<EntityRelation & { direction: string; peerType: string; peerId: number }>>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const loadingPeer = ref(false)
const saving = ref(false)
const createVisible = ref(false)
const queryType = ref('')
const queryId = ref<number>(1)

const filters = reactive({ sourceType: '', targetType: '', relationType: '' })

const form = reactive({
  sourceType: '',
  sourceId: 1,
  relationType: 'relates',
  targetType: '',
  targetId: 1,
  remark: '',
})

function typeLabel(type: string): string {
  return ENTITY_LABELS[type] ?? type
}

function relationLabel(type: string): string {
  return RELATION_LABELS[type] ?? type
}

async function loadTypes(): Promise<void> {
  try {
    const data = await relationApi.types()
    entityTypes.value = data.entityTypes ?? []
    relationTypes.value = data.relationTypes ?? []
    if (!queryType.value && entityTypes.value.length) {
      queryType.value = entityTypes.value[0]
    }
  } catch {
    entityTypes.value = []
    relationTypes.value = []
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const page = await relationApi.list({
      sourceType: filters.sourceType,
      targetType: filters.targetType,
      relationType: filters.relationType,
      pageNum: pageNum.value,
      pageSize: pageSize.value,
    })
    rows.value = page.list ?? []
    total.value = page.total ?? 0
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function reload(): void {
  pageNum.value = 1
  void load()
}

function resetFilters(): void {
  filters.sourceType = ''
  filters.targetType = ''
  filters.relationType = ''
  reload()
}

async function loadPeer(): Promise<void> {
  if (!queryType.value) {
    ElMessage.warning('请选择实体类型')
    return
  }
  loadingPeer.value = true
  try {
    const data = await relationApi.of(queryType.value, queryId.value)
    peerList.value = data.list ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '查询失败')
  } finally {
    loadingPeer.value = false
  }
}

function onTypeChange(): void {
  peerList.value = []
}

function openCreate(): void {
  form.sourceType = entityTypes.value[0] ?? ''
  form.sourceId = 1
  form.relationType = relationTypes.value[0] ?? 'relates'
  form.targetType = entityTypes.value[1] ?? entityTypes.value[0] ?? ''
  form.targetId = 1
  form.remark = ''
  createVisible.value = true
}

async function save(): Promise<void> {
  if (!form.sourceType || !form.targetType || !form.relationType) {
    ElMessage.warning('来源、关系、目标为必填')
    return
  }
  saving.value = true
  try {
    await relationApi.create({ ...form })
    ElMessage.success('已建立关联')
    createVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(id: number): Promise<void> {
  try {
    await relationApi.remove(id)
    ElMessage.success('已删除')
    peerList.value = peerList.value.filter((item) => item.id !== id)
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

onMounted(async () => {
  await loadTypes()
  await load()
})
</script>

<style scoped>
.relation-layout {
  display: grid;
  grid-template-columns: minmax(300px, 400px) 1fr;
  gap: 12px;
  align-items: start;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  font-size: 15px;
}

.hint {
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.6;
}

.peer-table {
  margin-top: 12px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
