<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">用例树</h2>
      <div class="toolbar" style="margin: 0">
        <el-select
          v-model="projectId"
          placeholder="选择项目"
          style="width: 200px"
          @change="load"
        >
          <el-option
            v-for="item in appStore.projects"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="tree-layout">
      <div class="panel panel-pad">
        <h3 class="section-title">
          功能点
          <el-tag size="small" effect="plain">{{ total }} 条用例</el-tag>
        </h3>
        <el-input
          v-model="keyword"
          placeholder="按功能点或用例过滤"
          clearable
          class="tree-filter"
        />
        <el-tree
          v-loading="loading"
          ref="treeRef"
          :data="tree"
          :props="{ label: 'name', children: 'children' }"
          node-key="id"
          highlight-current
          :default-expand-all="false"
          :filter-node-method="filterNode"
          @node-click="onNodeClick"
        >
          <template #default="{ data }">
            <span class="tree-node">
              <span class="tree-node__name">{{ data.name }}</span>
              <span class="tree-node__count">{{ data.caseCount ?? 0 }}</span>
            </span>
          </template>
        </el-tree>
        <el-empty v-if="!loading && !tree.length" description="该项目还没有功能点" :image-size="70" />
      </div>

      <div class="panel panel-pad">
        <h3 class="section-title">
          {{ currentNode?.path || '用例明细' }}
          <el-tag v-if="currentNode" size="small" effect="plain">
            {{ currentCases.length }} 条
          </el-tag>
          <el-tag v-if="currentNode" size="small" type="success" effect="plain">
            通过 {{ currentNode.passCount }}
          </el-tag>
          <el-tag v-if="currentNode" size="small" type="danger" effect="plain">
            失败 {{ currentNode.failCount }}
          </el-tag>
        </h3>

        <el-table v-if="currentCases.length" :data="currentCases" border size="small">
          <el-table-column prop="caseNo" label="编号" width="120" class-name="text-mono" />
          <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
          <el-table-column prop="caseType" label="类型" width="100" />
          <el-table-column prop="priority" label="优先级" width="86" align="center" />
          <el-table-column label="状态" width="100">
            <template #default="scope">
              <el-tag size="small" :type="statusTagType(scope.row.status)">
                {{ scope.row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="scope">
              <el-button link type="primary" @click="openCase(scope.row.id)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="选择一个功能点查看用例" :image-size="80" />

        <div v-if="orphans.length" class="orphans">
          <h4 class="orphans__title">未挂功能点的用例（{{ orphans.length }}）</h4>
          <el-table :data="orphans" border size="small" max-height="260">
            <el-table-column prop="caseNo" label="编号" width="120" class-name="text-mono" />
            <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
            <el-table-column prop="priority" label="优先级" width="86" align="center" />
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="scope">
                <el-button link type="primary" @click="openCase(scope.row.id)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { caseApi } from '@/api'
import type { CaseTreeNode, TestCase } from '@/api/types'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const appStore = useAppStore()

const tree = ref<CaseTreeNode[]>([])
const orphans = ref<TestCase[]>([])
const total = ref(0)
const loading = ref(false)
const keyword = ref('')
const projectId = ref<number | null>(null)
const currentNode = ref<CaseTreeNode | null>(null)
const treeRef = ref<{ filter: (value: string) => void } | null>(null)

const currentCases = computed(() => currentNode.value?.cases ?? [])

function statusTagType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'pass') return 'success'
  if (status === 'fail') return 'danger'
  if (status === 'blocked') return 'warning'
  return 'info'
}

function filterNode(value: string, data: CaseTreeNode): boolean {
  if (!value) {
    return true
  }
  const key = value.toLowerCase()
  if (String(data.name ?? '').toLowerCase().includes(key)) {
    return true
  }
  return (data.cases ?? []).some((item) =>
    [item.title, item.caseNo].some((field) => String(field ?? '').toLowerCase().includes(key)),
  )
}

function onNodeClick(data: CaseTreeNode): void {
  currentNode.value = data
}

function openCase(id: number): void {
  void router.push(`/test-cases/${id}`)
}

async function load(): Promise<void> {
  if (!projectId.value) {
    ElMessage.warning('请选择项目')
    return
  }
  loading.value = true
  try {
    const data = await caseApi.tree(projectId.value)
    tree.value = data.list ?? []
    orphans.value = data.orphans ?? []
    total.value = data.total ?? 0
    currentNode.value = null
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

watch(keyword, (value) => {
  treeRef.value?.filter(value)
})

watch(
  () => appStore.currentProjectId,
  (value) => {
    if (value && value !== projectId.value) {
      projectId.value = value
      void load()
    }
  },
)

onMounted(async () => {
  await appStore.loadProjects()
  projectId.value = appStore.currentProjectId
  await load()
})
</script>

<style scoped>
.tree-layout {
  display: grid;
  grid-template-columns: minmax(280px, 360px) 1fr;
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

.tree-filter {
  margin-bottom: 10px;
}

.tree-node {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding-right: 8px;
}

.tree-node__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tree-node__count {
  color: var(--aitp-text-weak);
  font-size: 12px;
}

.orphans {
  margin-top: 18px;
}

.orphans__title {
  margin: 0 0 8px;
  font-size: 14px;
}
</style>
