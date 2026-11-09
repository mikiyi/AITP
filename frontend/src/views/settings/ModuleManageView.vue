<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">模块树设置</h2>
      <div class="toolbar" style="margin: 0">
        <el-select v-model="projectId" placeholder="选择项目" style="width: 200px" @change="onProjectChange">
          <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button type="primary" :icon="Plus" @click="openCreate">新增节点</el-button>
      </div>
    </div>

    <div class="panel panel-pad module-types-panel">
      <div class="module-types-head">
        <h3>层级角色名</h3>
        <el-button type="primary" size="small" :loading="savingTypes" @click="saveModuleTypes">保存</el-button>
      </div>
      <el-form label-width="140px" inline>
        <el-form-item label="模块层级角色名">
          <el-input v-model="moduleTypes" placeholder="module,submodule,feature" style="width: 280px" />
          <div class="muted module-types-hint">逗号分隔，依次对应模块、子模块、功能</div>
        </el-form-item>
      </el-form>
    </div>

    <div class="module-layout">
      <div class="panel panel-pad module-tree-panel">
        <ModuleTree
          ref="moduleTreeRef"
          :nodes="tree"
          :loading="loading"
          :load-cases="loadNodeCases"
          @select="onSelect"
          @open-case="openCaseDetail"
        />
      </div>

      <div class="panel panel-pad module-detail">
        <el-empty v-if="!current" description="请选择左侧节点" :image-size="90" />
        <template v-else>
          <div class="module-detail__head">
            <el-tag size="small" effect="plain">{{ typeLabel(current.nodeType) }}</el-tag>
            <h3>{{ current.name }}</h3>
            <span class="muted text-mono">{{ current.path }}</span>
          </div>

          <el-form label-width="110px" class="module-detail__form">
            <el-form-item label="名称">
              <el-input v-model="editForm.name" />
            </el-form-item>
            <el-form-item label="层级类型">
              <el-select v-model="editForm.nodeType">
                <el-option label="模块" value="module" />
                <el-option label="子模块" value="submodule" />
                <el-option label="功能" value="feature" />
              </el-select>
            </el-form-item>
            <el-form-item label="编码">
              <el-input v-model="editForm.code" placeholder="可选" />
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="editForm.description" type="textarea" :rows="3" />
            </el-form-item>
            <el-form-item label="父节点">
              <el-select v-model="parentId" filterable clearable placeholder="（顶级）">
                <el-option v-for="item in flatNodes" :key="item.id" :label="item.path" :value="item.id" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="saving" @click="saveNode">保存</el-button>
              <el-button :loading="moving" @click="moveNode">换父节点</el-button>
              <el-button type="danger" :loading="removing" @click="removeNode">删除</el-button>
            </el-form-item>
          </el-form>

          <el-divider>功能点 AI 能力</el-divider>
          <p class="muted module-detail__hint">
            启用后该节点可用对应 AI 能力；填写专属提示词 code 可覆盖全局提示词。
          </p>
          <div class="cap-list">
            <div v-for="item in capabilities" :key="item.code" class="cap-row">
              <el-switch v-model="item.enabled" />
              <span class="cap-row__label">{{ item.label }}</span>
              <el-input
                v-model="item.promptCode"
                size="small"
                placeholder="专属提示词 code（可空）"
                style="width: 220px"
              />
            </div>
          </div>
          <el-button type="primary" size="small" :loading="savingCaps" @click="saveCapabilities">
            保存能力绑定
          </el-button>
        </template>
      </div>
    </div>

    <el-dialog v-model="dialogVisible" title="新增模块节点" width="520px">
      <el-form :model="createForm" label-width="110px">
        <el-form-item label="名称" required>
          <el-input v-model="createForm.name" />
        </el-form-item>
        <el-form-item label="层级类型">
          <el-select v-model="createForm.nodeType">
            <el-option label="模块" value="module" />
            <el-option label="子模块" value="submodule" />
            <el-option label="功能" value="feature" />
          </el-select>
        </el-form-item>
        <el-form-item label="父节点">
          <el-select v-model="createForm.parentId" clearable filterable placeholder="（顶级）">
            <el-option v-for="item in flatNodes" :key="item.id" :label="item.path" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="createForm.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createNode">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import ModuleTree, { type ModuleTreeExpose } from '@/components/ModuleTree.vue'
import { caseApi, labApi, moduleApi, settingsApi } from '@/api'
import type { PagePayload } from '@/api/request'
import { NODE_CASE_PAGE_SIZE } from '@/composables/useNodeCases'
import type { CapabilitySpec, ModuleNode, TestCase } from '@/api/types'
import { useAppStore } from '@/stores/app'

interface CapabilityRow {
  code: string
  label: string
  enabled: boolean
  promptCode: string
}

const appStore = useAppStore()
const router = useRouter()

const projectId = ref<number | null>(null)
const tree = ref<ModuleNode[]>([])
const moduleTreeRef = ref<ModuleTreeExpose | null>(null)
const loading = ref(false)
const current = ref<ModuleNode | null>(null)

const MODULE_TYPES_KEY = 'module.default_types'
const moduleTypes = ref('module,submodule,feature')
const savingTypes = ref(false)

const saving = ref(false)
const moving = ref(false)
const removing = ref(false)
const savingCaps = ref(false)

const editForm = reactive({ name: '', nodeType: 'module', code: '', description: '' })
const parentId = ref<number | null>(null)

const dialogVisible = ref(false)
const creating = ref(false)
const createForm = reactive({
  name: '',
  nodeType: 'module',
  parentId: null as number | null,
  description: '',
})

const capabilities = ref<CapabilityRow[]>([])

const flatNodes = computed(() => {
  const result: ModuleNode[] = []
  const walk = (list: ModuleNode[]): void => {
    list.forEach((node) => {
      result.push(node)
      if (node.children?.length) {
        walk(node.children)
      }
    })
  }
  walk(tree.value)
  return result
})

function typeLabel(nodeType: string): string {
  if (nodeType === 'module') {
    return '模块'
  }
  if (nodeType === 'submodule') {
    return '子模块'
  }
  return '功能'
}

async function loadTree(): Promise<void> {
  if (!projectId.value) {
    tree.value = []
    moduleTreeRef.value?.resetDrilldown()
    return
  }
  loading.value = true
  try {
    const payload = await moduleApi.tree(projectId.value)
    tree.value = payload.tree ?? []
    // 节点增删改后重拉树：旧钻取缓存的页码不再可信
    moduleTreeRef.value?.invalidateCases()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载模块树失败')
    tree.value = []
  } finally {
    loading.value = false
  }
}

/** 节点用例钻取：moduleId 取本级，与节点上的「用例 N」引用数同口径；一页 100 条按 pageNum 续取。 */
function loadNodeCases(node: ModuleNode, pageNum: number): Promise<PagePayload<TestCase>> {
  return caseApi.list({
    projectId: projectId.value ?? undefined,
    moduleId: String(node.id),
    pageNum,
    pageSize: NODE_CASE_PAGE_SIZE,
  })
}

/** 面板里的用例行：点标题进用例详情，与用例列表的行点击同一条路径。 */
function openCaseDetail(item: TestCase): void {
  void router.push(`/test-cases/${item.id}`)
}

async function onSelect(node: ModuleNode): Promise<void> {
  current.value = node
  Object.assign(editForm, {
    name: node.name,
    nodeType: node.nodeType,
    code: node.code ?? '',
    description: node.description ?? '',
  })
  parentId.value = node.parentId
  await loadCapabilities(node.id)
}

async function loadCapabilities(nodeId: number): Promise<void> {
  try {
    const [all, bound] = await Promise.all([labApi.capabilities(), moduleApi.capabilities(nodeId)])
    const boundMap = new Map<string, { enabled: boolean; promptCode: string | null }>()
    const payload = bound as { capabilities?: Array<{ capability: string; enabled: boolean; promptCode: string | null }> }
    ;(payload.capabilities ?? []).forEach((item) => {
      boundMap.set(item.capability, { enabled: item.enabled, promptCode: item.promptCode })
    })
    capabilities.value = (all as CapabilitySpec[])
      .filter((item) => item.code !== 'prompt_optimize')
      .map((item) => {
        const hit = boundMap.get(item.code)
        return {
          code: item.code,
          label: item.label,
          enabled: hit?.enabled ?? false,
          promptCode: hit?.promptCode ?? '',
        }
      })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载能力绑定失败')
    capabilities.value = []
  }
}

async function saveNode(): Promise<void> {
  if (!current.value) {
    return
  }
  saving.value = true
  try {
    await moduleApi.update(current.value.id, {
      name: editForm.name,
      nodeType: editForm.nodeType,
      code: editForm.code,
      description: editForm.description,
    })
    ElMessage.success('已保存')
    await loadTree()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function moveNode(): Promise<void> {
  if (!current.value) {
    return
  }
  moving.value = true
  try {
    await moduleApi.move(current.value.id, parentId.value)
    ElMessage.success('已换父节点')
    current.value = null
    await loadTree()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '换父失败')
  } finally {
    moving.value = false
  }
}

async function removeNode(): Promise<void> {
  if (!current.value) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认删除节点「${current.value.name}」？存在子节点或被用例/缺陷引用时会被拒绝。`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  removing.value = true
  try {
    await moduleApi.remove(current.value.id)
    ElMessage.success('已删除')
    current.value = null
    await loadTree()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  } finally {
    removing.value = false
  }
}

async function saveCapabilities(): Promise<void> {
  if (!current.value) {
    return
  }
  savingCaps.value = true
  try {
    await moduleApi.setCapabilities(
      current.value.id,
      capabilities.value.map((item) => ({
        capability: item.code,
        enabled: item.enabled,
        promptCode: item.promptCode || null,
      })),
    )
    ElMessage.success('能力绑定已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    savingCaps.value = false
  }
}

function openCreate(): void {
  Object.assign(createForm, {
    name: '',
    nodeType: current.value?.nodeType ?? 'module',
    parentId: current.value?.id ?? null,
    description: '',
  })
  dialogVisible.value = true
}

async function createNode(): Promise<void> {
  if (!createForm.name.trim()) {
    ElMessage.warning('名称为必填项')
    return
  }
  if (!projectId.value) {
    ElMessage.warning('请先选择项目')
    return
  }
  creating.value = true
  try {
    await moduleApi.create({
      projectId: projectId.value,
      name: createForm.name,
      nodeType: createForm.nodeType,
      parentId: createForm.parentId,
      description: createForm.description,
    })
    ElMessage.success('已创建')
    dialogVisible.value = false
    await loadTree()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建失败')
  } finally {
    creating.value = false
  }
}

/** 页面里的项目下拉与顶栏共用同一份选择，两处切换都要互相跟随。 */
function onProjectChange(value: number | null): void {
  if (value) {
    appStore.selectProject(value)
  }
  void loadTree()
}

watch(
  () => appStore.currentProjectId,
  (value) => {
    if (value && value !== projectId.value) {
      projectId.value = value
      void loadTree()
    }
  },
  { immediate: true },
)

/** 加载模块层级角色名配置。 */
async function loadModuleTypes(): Promise<void> {
  try {
    const configs = await settingsApi.config()
    moduleTypes.value =
      configs.find((item) => item.key === MODULE_TYPES_KEY)?.value || 'module,submodule,feature'
  } catch {
    // 加载失败保留默认值，不阻塞模块树
  }
}

async function saveModuleTypes(): Promise<void> {
  savingTypes.value = true
  try {
    await settingsApi.updateConfig([
      { key: MODULE_TYPES_KEY, value: moduleTypes.value.trim(), valueType: 'string' },
    ])
    ElMessage.success('已保存')
    await loadModuleTypes()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    savingTypes.value = false
  }
}

const activatedOnce = ref(false)

onMounted(() => {
  void loadModuleTypes()
})
onActivated(() => {
  // keep-alive 首次激活与 mount 同时发生，跳过以免重复拉取
  if (activatedOnce.value) {
    void loadTree()
    void loadModuleTypes()
  }
  activatedOnce.value = true
})
</script>

<style scoped>
.module-types-panel {
  margin-bottom: 14px;
}

.module-types-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.module-types-head h3 {
  margin: 0;
  font-size: 15px;
}

.module-types-hint {
  font-size: 12px;
  line-height: 1.5;
  margin-top: 4px;
}

.module-layout {
  display: grid;
  grid-template-columns: minmax(280px, 360px) 1fr;
  gap: 12px;
  align-items: start;
}

.module-tree-panel {
  min-height: 420px;
}

.module-detail {
  min-height: 420px;
}

.module-detail__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.module-detail__head h3 {
  margin: 0;
  font-size: 16px;
}

.module-detail__form {
  max-width: 520px;
}

.module-detail__hint {
  font-size: 12px;
  margin: 0 0 10px;
}

.cap-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}

.cap-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.cap-row__label {
  width: 140px;
}
</style>
