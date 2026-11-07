<template>
  <div class="page todo-page">
    <div class="todo-stats">
      <div class="todo-stat">
        <span class="todo-stat__label">未完成</span>
        <span class="todo-stat__value">{{ summary.active }}</span>
      </div>
      <div class="todo-stat">
        <span class="todo-stat__label">今天到期</span>
        <span class="todo-stat__value">{{ summary.dueToday }}</span>
      </div>
      <div class="todo-stat" :class="{ 'is-danger': summary.overdue > 0 }">
        <span class="todo-stat__label">逾期</span>
        <span class="todo-stat__value">{{ summary.overdue }}</span>
      </div>
      <div class="todo-stat">
        <span class="todo-stat__label">已完成</span>
        <span class="todo-stat__value">{{ summary.done }}</span>
      </div>
      <div class="todo-stat todo-stat--wide">
        <span class="todo-stat__label">完成率</span>
        <el-progress
          :percentage="(summary.completionRate || 0) * 100"
          :format="percentText"
          :stroke-width="8"
          :show-text="false"
          class="todo-stat__bar"
        />
        <span class="todo-stat__value">{{ percentText((summary.completionRate || 0) * 100) }}</span>
      </div>
    </div>

    <div class="todo-layout">
      <!-- 左：视图与分类 -->
      <aside class="todo-rail panel panel-pad">
        <div class="todo-rail__title">视图</div>
        <ul class="todo-rail__list">
          <li
            v-for="item in VIEWS"
            :key="item.key"
            class="todo-rail__item"
            :class="{ 'is-active': view === item.key }"
            @click="setView(item.key)"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <span class="todo-rail__label">{{ item.label }}</span>
            <span class="todo-rail__count">{{ viewCount(item.key) }}</span>
          </li>
        </ul>

        <div class="todo-rail__title">分类</div>
        <ul class="todo-rail__list">
          <li
            class="todo-rail__item"
            :class="{ 'is-active': category === '' }"
            @click="setCategory('')"
          >
            <el-icon><Folder /></el-icon>
            <span class="todo-rail__label">全部分类</span>
            <span class="todo-rail__count">{{ summary.total }}</span>
          </li>
          <li
            v-for="item in categories"
            :key="item.category"
            class="todo-rail__item"
            :class="{ 'is-active': category === item.category }"
            @click="setCategory(item.category)"
          >
            <el-icon><CollectionTag /></el-icon>
            <span class="todo-rail__label">{{ item.category }}</span>
            <span class="todo-rail__count">{{ item.active }}</span>
          </li>
        </ul>
        <el-empty v-if="!categories.length" description="还没有分类" :image-size="50" />
      </aside>

      <!-- 中：清单 -->
      <section class="todo-main panel panel-pad">
        <div class="todo-quick">
          <el-input
            v-model="quickTitle"
            placeholder="添加一条任务，回车直接存"
            :prefix-icon="EditPen"
            @keyup.enter="quickAdd"
          />
          <el-select v-model="quickPriority" class="todo-quick__field">
            <el-option v-for="item in PRIORITIES" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-date-picker
            v-model="quickDue"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="到期日"
            class="todo-quick__date"
          />
          <el-button type="primary" :icon="Plus" @click="quickAdd">添加</el-button>
        </div>

        <div class="toolbar">
          <el-input
            v-model="keyword"
            placeholder="搜索任务"
            clearable
            style="width: 160px"
            @input="scheduleQuery"
            @keyup.enter="reload"
            @clear="reload"
          />
          <el-select v-model="status" placeholder="状态" clearable style="width: 110px" @change="reload">
            <el-option v-for="item in STATUSES" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-select v-model="priority" placeholder="优先级" clearable style="width: 110px" @change="reload">
            <el-option v-for="item in PRIORITIES" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-select v-model="projectId" placeholder="项目" clearable style="width: 130px" @change="onFilterProjectChange">
            <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
          <el-select
            v-model="versionId"
            placeholder="版本"
            :no-data-text="projectId ? '该项目暂无版本' : '先选项目'"
            clearable
            :disabled="!projectId"
            style="width: 120px"
            @change="reload"
          >
            <el-option v-for="item in filterVersionOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-button :icon="RefreshLeft" @click="reset">重置</el-button>
          <div class="spacer" />
          <el-button :icon="Tickets" @click="selectMode = !selectMode">批量</el-button>
          <BaseExportMenu :resolve-url="resolveExportUrl" />
        </div>

        <div v-if="selectMode" class="todo-batch">
          <span class="muted">已选 {{ selected.length }} 项</span>
          <el-button size="small" :icon="Select" @click="batchStatus('done')">标记完成</el-button>
          <el-select
            v-model="batchPriority"
            placeholder="改优先级"
            size="small"
            style="width: 120px"
            @change="batchSetPriority"
          >
            <el-option v-for="item in PRIORITIES" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-button size="small" type="danger" plain :icon="Delete" @click="batchRemove">删除</el-button>
          <el-button size="small" link @click="clearSelection">取消选择</el-button>
        </div>

        <el-empty v-if="!todos.length" description="还没有任务，在上面输入一条试试" :image-size="90" />

        <div v-else class="todo-list">
          <div
            v-for="todo in todos"
            :key="todo.id"
            class="todo-row"
            :class="{
              'is-done': todo.status === 'done',
              'is-cancelled': todo.status === 'cancelled',
              'is-active': current?.id === todo.id,
            }"
          >
            <el-checkbox
              v-if="selectMode"
              :model-value="selected.includes(todo.id)"
              @change="toggleSelect(todo.id)"
            />
            <el-checkbox
              v-else
              :model-value="todo.status === 'done'"
              @change="toggle(todo)"
            />
            <span class="todo-row__bar" :style="{ background: priorityColor(todo.priority) }" />
            <span class="todo-row__title" @click="select(todo)">{{ todo.title }}</span>
            <el-tag v-if="todo.isPinned" size="small" type="warning" effect="plain">置顶</el-tag>
            <el-tag v-if="todo.category" size="small" effect="plain">{{ todo.category }}</el-tag>
            <el-tag v-if="todo.versionCode" size="small" type="info" effect="plain">{{ todo.versionCode }}</el-tag>
            <el-tag
              v-if="todo.dueDate"
              size="small"
              effect="plain"
              :type="todo.isOverdue ? 'danger' : todo.isDueToday ? 'warning' : 'info'"
            >
              {{ dueText(todo) }}
            </el-tag>
            <span v-if="todo.childTotal" class="todo-row__sub muted">
              {{ todo.childDone }}/{{ todo.childTotal }}
            </span>
            <span v-if="todo.progress > 0 && todo.status !== 'done'" class="todo-row__progress muted">
              {{ todo.progress }}%
            </span>
            <el-dropdown trigger="click" @command="onCommand($event, todo)">
              <el-button text :icon="MoreFilled" />
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="edit">编辑</el-dropdown-item>
                  <el-dropdown-item command="subtask">加子任务</el-dropdown-item>
                  <el-dropdown-item command="up">上移</el-dropdown-item>
                  <el-dropdown-item command="down">下移</el-dropdown-item>
                  <el-dropdown-item command="pin" divided>{{ todo.isPinned ? '取消置顶' : '置顶' }}</el-dropdown-item>
                  <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>

          <div v-if="expanded.size" class="todo-children">
            <div v-for="todo in todos" :key="`c-${todo.id}`">
              <template v-if="expanded.has(todo.id)">
                <div
                  v-for="child in todo.children || []"
                  :key="child.id"
                  class="todo-row todo-row--child"
                  :class="{ 'is-done': child.status === 'done' }"
                >
                  <el-checkbox :model-value="child.status === 'done'" @change="toggle(child)" />
                  <span class="todo-row__bar" :style="{ background: priorityColor(child.priority) }" />
                  <span class="todo-row__title" @click="select(child)">{{ child.title }}</span>
                  <el-tag
                    v-if="child.dueDate"
                    size="small"
                    effect="plain"
                    :type="child.isOverdue ? 'danger' : 'info'"
                  >
                    {{ dueText(child) }}
                  </el-tag>
                  <el-button
                    text
                    :icon="Delete"
                    size="small"
                    @click="remove(child)"
                  />
                </div>
                <el-button
                  v-if="(todo.children || []).length"
                  link
                  type="primary"
                  class="todo-children__toggle"
                  @click="toggleExpand(todo.id)"
                >
                  收起子任务
                </el-button>
              </template>
            </div>
          </div>
        </div>

        <div class="todo-pager">
          <el-pagination
            v-model:current-page="pageNum"
            v-model:page-size="pageSize"
            :total="total"
            :page-sizes="[10, 20, 50, 100]"
            layout="total, sizes, prev, pager, next, jumper"
            background
            @current-change="load"
            @size-change="reload"
          />
        </div>
      </section>

      <!-- 右：详情 -->
      <aside class="todo-detail panel panel-pad">
        <el-empty v-if="!current" description="选一条任务看详情" :image-size="80" />
        <el-form v-else label-width="76px" size="small" class="todo-detail__form">
          <div class="todo-detail__head">
            <span class="muted">{{ current.todoNo }}</span>
            <el-button text type="danger" :icon="Delete" @click="remove(current)" />
          </div>
          <el-form-item label="标题">
            <el-input v-model="form.title" />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="form.status" style="width: 100%">
              <el-option v-for="item in STATUSES" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="优先级">
            <el-select v-model="form.priority" style="width: 100%">
              <el-option v-for="item in PRIORITIES" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="到期日">
            <el-date-picker
              v-model="form.dueDate"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="不设到期"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="分类">
            <el-input v-model="form.category" placeholder="例如：缺陷跟进" />
          </el-form-item>
          <el-form-item label="所属项目">
            <el-select v-model="form.projectId" placeholder="不关联项目" clearable style="width: 100%" @change="onFormProjectChange">
              <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="版本">
            <el-select v-model="form.versionId" placeholder="不指定版本" clearable style="width: 100%">
              <el-option v-for="item in formVersionOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="formVersionName" label="版本名称">
            <span class="muted">{{ formVersionName }}</span>
          </el-form-item>
          <el-form-item label="标签">
            <el-input v-model="form.tags" placeholder="逗号分隔" />
          </el-form-item>
          <el-form-item label="进度">
            <el-slider v-model="form.progress" :min="0" :max="100" />
          </el-form-item>
          <el-form-item label="说明">
            <el-input v-model="form.description" type="textarea" :rows="4" />
          </el-form-item>

          <div class="todo-detail__section">
            <div class="todo-detail__label">
              子任务
              <el-button link type="primary" :icon="Plus" @click="addSubtask">添加</el-button>
            </div>
            <div v-if="!(current.children || []).length" class="muted">还没有子任务</div>
            <div v-for="child in current.children || []" :key="child.id" class="todo-detail__child">
              <el-checkbox
                :model-value="child.status === 'done'"
                @change="toggle(child)"
              />
              <span :class="{ 'is-done': child.status === 'done' }">{{ child.title }}</span>
              <el-button text :icon="Delete" size="small" @click="remove(child)" />
            </div>
          </div>

          <div class="todo-detail__actions">
            <el-button type="primary" :loading="saving" @click="save">保存</el-button>
            <el-button :icon="Star" @click="togglePin">{{ current.isPinned ? '取消置顶' : '置顶' }}</el-button>
          </div>
        </el-form>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  CollectionTag,
  Delete,
  EditPen,
  Folder,
  MoreFilled,
  Plus,
  RefreshLeft,
  Select,
  Star,
  Tickets,
} from '@element-plus/icons-vue'
import BaseExportMenu from '@/components/BaseExportMenu.vue'
import { projectVersionApi, todoApi } from '@/api'
import { percentText } from '@/utils/format'
import type { Todo, TodoSummary } from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useAutoQuery } from '@/composables/useAutoQuery'

interface Option {
  value: string
  label: string
}

interface ViewItem {
  key: string
  label: string
  icon: string
}

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()

const VIEWS: ViewItem[] = [
  { key: 'all', label: '全部任务', icon: 'List' },
  { key: 'today', label: '今天到期', icon: 'Calendar' },
  { key: 'week', label: '本周到期', icon: 'Clock' },
  { key: 'overdue', label: '已逾期', icon: 'Warning' },
  { key: 'starred', label: '置顶', icon: 'Star' },
  { key: 'done', label: '已完成', icon: 'CircleCheck' },
]

const STATUSES: Option[] = [
  { value: 'open', label: '待办' },
  { value: 'doing', label: '进行中' },
  { value: 'done', label: '已完成' },
  { value: 'cancelled', label: '已取消' },
]

const PRIORITIES: Option[] = [
  { value: 'urgent', label: '紧急' },
  { value: 'high', label: '高' },
  { value: 'medium', label: '中' },
  { value: 'low', label: '低' },
]

const PRIORITY_COLORS: Record<string, string> = {
  urgent: '#f56c6c',
  high: '#e6a23c',
  medium: '#2f6fed',
  low: '#909399',
}

const todos = ref<Todo[]>([])
const categories = ref<Array<{ category: string; total: number; active: number }>>([])
const summary = reactive<TodoSummary>({
  total: 0,
  active: 0,
  open: 0,
  doing: 0,
  done: 0,
  cancelled: 0,
  dueToday: 0,
  dueWeek: 0,
  overdue: 0,
  pinned: 0,
  completionRate: 0,
})
const total = ref(0)
const pageNum = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const saving = ref(false)

const view = ref('all')
const status = ref('')
const priority = ref('')
const category = ref('')
const keyword = ref('')
/** 项目筛选默认不过滤：任务清单是个人清单，不该被当前项目悄悄藏掉。 */
const projectId = ref<number | ''>('')
/** 版本只在选定项目后才有意义，每个项目的版本号会重名。 */
const versionId = ref<number | ''>('')

const filterVersionOptions = ref<Array<{ label: string; value: number }>>([])
const formVersionOptions = ref<Array<{ label: string; value: number; name: string }>>([])

async function versionsOf(project: number | '' | null): Promise<Array<{ label: string; value: number; name: string }>> {
  if (!project) return []
  try {
    const result = await projectVersionApi.list(Number(project))
    // 下拉统一只显版本号，版本名单独用只读行展示，避免选项文本过长撑破窄列
    return (result.list ?? []).map((v) => ({ label: v.versionCode, value: v.id as number, name: v.name || '' }))
  } catch {
    return []
  }
}

function onFilterProjectChange(): void {
  versionId.value = ''
  void versionsOf(projectId.value).then((items) => {
    filterVersionOptions.value = items
  })
  reload()
}

function onFormProjectChange(): void {
  form.versionId = null
  void versionsOf(form.projectId).then((items) => {
    formVersionOptions.value = items
  })
}

const formVersionName = computed(() => {
  const hit = formVersionOptions.value.find((item) => item.value === form.versionId)
  return hit?.name || ''
})

const quickTitle = ref('')
const quickPriority = ref('medium')
const quickDue = ref<string | null>(null)

const selectMode = ref(false)
const selected = ref<number[]>([])
const batchPriority = ref('')
const expanded = ref<Set<number>>(new Set())
const current = ref<Todo | null>(null)

const form = reactive({
  title: '',
  status: 'open',
  priority: 'medium',
  dueDate: '',
  category: '',
  tags: '',
  progress: 0,
  description: '',
  projectId: null as number | null,
  versionId: null as number | null,
})

/** 当前页的查询条件，导出与列表共用一份，导出的就是看到的。 */
function queryOf(): Record<string, unknown> {
  const query: Record<string, unknown> = { pageNum: pageNum.value, pageSize: pageSize.value }
  if (view.value !== 'all') query.view = view.value
  if (status.value) query.status = status.value
  if (priority.value) query.priority = priority.value
  if (category.value) query.category = category.value
  if (keyword.value.trim()) query.keyword = keyword.value.trim()
  if (projectId.value !== '') query.projectId = projectId.value
  if (versionId.value !== '') query.versionId = versionId.value
  return query
}

function viewCount(key: string): number {
  if (key === 'all') return summary.total
  if (key === 'today') return summary.dueToday
  if (key === 'week') return summary.dueWeek
  if (key === 'overdue') return summary.overdue
  if (key === 'starred') return summary.pinned
  if (key === 'done') return summary.done
  return 0
}

function priorityColor(value: string): string {
  return PRIORITY_COLORS[value] || PRIORITY_COLORS.medium
}

function dueText(todo: Todo): string {
  const due = todo.dueDate || ''
  if (todo.isOverdue) return `逾期 ${due.slice(5)}`
  if (todo.isDueToday) return '今天'
  return due.slice(5)
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const payload = await todoApi.list(queryOf())
    todos.value = payload.list ?? []
    total.value = payload.total ?? 0
    Object.assign(summary, payload.stats ?? {})
    const stat = await todoApi.stats(
      projectId.value !== '' ? { projectId: projectId.value } : {},
    )
    categories.value = stat.categories ?? []
  } catch (err) {
    todos.value = []
    total.value = 0
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

function reload(): void {
  pageNum.value = 1
  void load()
  void syncQuery()
}

const scheduleQuery = useAutoQuery(reload)

function reset(): void {
  status.value = ''
  priority.value = ''
  category.value = ''
  keyword.value = ''
  projectId.value = ''
  versionId.value = ''
  filterVersionOptions.value = []
  view.value = 'all'
  reload()
}

function setView(value: string): void {
  view.value = value
  reload()
}

function setCategory(value: string): void {
  category.value = value
  reload()
}

/** 筛选与分页写进地址栏，刷新或粘贴链接都能还原当前视图。 */
function syncQuery(): void {
  const query: Record<string, string> = {}
  if (view.value !== 'all') query.view = view.value
  if (status.value) query.status = status.value
  if (priority.value) query.priority = priority.value
  if (category.value) query.category = category.value
  if (keyword.value.trim()) query.keyword = keyword.value.trim()
  if (projectId.value !== '') query.project = String(projectId.value)
  if (versionId.value !== '') query.version = String(versionId.value)
  if (pageNum.value > 1) query.page = String(pageNum.value)
  if (pageSize.value !== 20) query.size = String(pageSize.value)
  void router.replace({ query })
}

function hydrateFromQuery(): void {
  const q = route.query
  const first = (value: unknown): string => (Array.isArray(value) ? String(value[0] ?? '') : String(value ?? ''))
  view.value = first(q.view) || 'all'
  status.value = first(q.status)
  priority.value = first(q.priority)
  category.value = first(q.category)
  keyword.value = first(q.keyword)
  const project = Number(first(q.project))
  projectId.value = Number.isInteger(project) && project > 0 ? project : ''
  const version = Number(first(q.version))
  versionId.value = Number.isInteger(version) && version > 0 ? version : ''
  void versionsOf(projectId.value).then((items) => {
    filterVersionOptions.value = items
  })
  const page = Number(first(q.page))
  pageNum.value = Number.isInteger(page) && page > 1 ? page : 1
  const size = Number(first(q.size))
  if ([10, 20, 50, 100].includes(size)) pageSize.value = size
}

function resolveExportUrl(format: string): string {
  const query = queryOf()
  query.format = format
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.append(key, String(value))
    }
  })
  return `/api/v1/todos/export?${params.toString()}`
}

async function quickAdd(): Promise<void> {
  const title = quickTitle.value.trim()
  if (!title) {
    ElMessage.warning('先写点内容再添加')
    return
  }
  try {
    const payload: Record<string, unknown> = { title, priority: quickPriority.value }
    if (quickDue.value) payload.dueDate = quickDue.value
    if (category.value) payload.category = category.value
    // 新任务默认挂在当前项目上；筛选里指定了项目就跟着筛选走
    const targetProject = projectId.value !== '' ? projectId.value : appStore.currentProjectId
    if (targetProject) payload.projectId = targetProject
    // 版本属于项目，只在筛选里真的选了项目时才跟着带，否则挂到别的项目的版本上
    if (projectId.value !== '' && versionId.value !== '') payload.versionId = versionId.value
    await todoApi.create(payload)
    quickTitle.value = ''
    quickDue.value = null
    ElMessage.success('已添加')
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '添加失败')
  }
}

async function toggle(todo: Todo): Promise<void> {
  try {
    await todoApi.toggle(todo.id)
    await load()
    // 勾选子任务会回推父任务状态与进度，详情里两份数据都要重新取。
    const currentId = current.value?.id
    if (currentId === todo.id || currentId === todo.parentId) {
      await refreshCurrent()
      if (current.value) syncRollupFields(current.value)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '操作失败')
  }
}

function toggleSelect(id: number): void {
  selected.value = selected.value.includes(id)
    ? selected.value.filter((item) => item !== id)
    : [...selected.value, id]
}

function clearSelection(): void {
  selected.value = []
}

async function batchStatus(value: string): Promise<void> {
  if (!selected.value.length) {
    ElMessage.warning('先选几条任务')
    return
  }
  try {
    await todoApi.batchUpdate(selected.value, { status: value })
    ElMessage.success(`已更新 ${selected.value.length} 条`)
    selected.value = []
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量更新失败')
  }
}

async function batchSetPriority(value: string): Promise<void> {
  if (!value || !selected.value.length) return
  try {
    await todoApi.batchUpdate(selected.value, { priority: value })
    batchPriority.value = ''
    ElMessage.success(`已更新 ${selected.value.length} 条`)
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量更新失败')
  }
}

async function batchRemove(): Promise<void> {
  if (!selected.value.length) {
    ElMessage.warning('先选几条任务')
    return
  }
  try {
    await ElMessageBox.confirm(`确认删除选中的 ${selected.value.length} 条任务？`, '删除确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await todoApi.batchDelete(selected.value)
    ElMessage.success('已删除')
    selected.value = []
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量删除失败')
  }
}

function toggleExpand(id: number): void {
  const next = new Set(expanded.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  expanded.value = next
}

function fillForm(todo: Todo): void {
  form.title = todo.title
  form.status = todo.status
  form.priority = todo.priority
  form.dueDate = todo.dueDate || ''
  form.category = todo.category || ''
  form.tags = todo.tags || ''
  form.progress = todo.progress
  form.description = todo.description || ''
  form.projectId = todo.projectId ?? null
  form.versionId = Number(todo.versionId ?? 0) || null
  void versionsOf(form.projectId).then((items) => {
    formVersionOptions.value = items
  })
}

function select(todo: Todo): void {
  current.value = todo
  fillForm(todo)
}

/** 子任务增删、勾选、保存与置顶后，详情里的子任务与回推状态都要重新取一次。 */
async function refreshCurrent(): Promise<void> {
  if (!current.value) return
  const id = current.value.id
  try {
    const detail = await todoApi.get(id)
    if (current.value?.id === id) {
      current.value = detail
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '刷新任务详情失败')
  }
}

/** 只同步会被子任务回推的字段，避免覆盖表单里尚未保存的其他修改。 */
function syncRollupFields(todo: Todo): void {
  form.status = todo.status
  form.progress = todo.progress
}

async function save(): Promise<void> {
  if (!current.value) return
  if (!form.title.trim()) {
    ElMessage.warning('标题不能为空')
    return
  }
  saving.value = true
  try {
    const updated = await todoApi.update(current.value.id, {
      title: form.title.trim(),
      status: form.status,
      priority: form.priority,
      dueDate: form.dueDate || '',
      category: form.category,
      tags: form.tags,
      progress: form.progress,
      description: form.description,
      projectId: form.projectId ?? '',
      versionId: form.versionId ?? '',
    })
    current.value = updated
    fillForm(updated)
    ElMessage.success('已保存')
    await load()
    await refreshCurrent()
    if (current.value) syncRollupFields(current.value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function togglePin(): Promise<void> {
  if (!current.value) return
  try {
    const updated = await todoApi.update(current.value.id, { isPinned: !current.value.isPinned })
    current.value = updated
    await load()
    await refreshCurrent()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '操作失败')
  }
}

async function addSubtask(): Promise<void> {
  if (!current.value) return
  try {
    const { value } = await ElMessageBox.prompt('写一条子任务', '添加子任务', {
      confirmButtonText: '添加',
      cancelButtonText: '取消',
      inputValue: '',
    })
    const title = String(value ?? '').trim()
    if (!title) return
    await todoApi.create({ title, parentId: current.value.id })
    ElMessage.success('已添加子任务')
    await load()
    await refreshCurrent()
    if (current.value) syncRollupFields(current.value)
  } catch {
    // 用户取消，不提示
  }
}

async function remove(todo: Todo): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除「${todo.title}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  const selectedId = current.value?.id
  const selectedParentId = current.value?.parentId
  try {
    await todoApi.remove(todo.id)
    if (selectedId === todo.id || selectedParentId === todo.id) {
      current.value = null
    }
    ElMessage.success('已删除')
    await load()
    if (current.value && selectedId === todo.parentId) {
      await refreshCurrent()
      if (current.value) syncRollupFields(current.value)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

async function onCommand(raw: string | number | object, todo: Todo): Promise<void> {
  const command = String(raw)
  if (command === 'edit') {
    select(todo)
    return
  }
  if (command === 'subtask') {
    select(todo)
    await addSubtask()
    return
  }
  if (command === 'pin') {
    await todoApi.update(todo.id, { isPinned: !todo.isPinned })
    await load()
    if (current.value?.id === todo.id) {
      await refreshCurrent()
    }
    return
  }
  if (command === 'delete') {
    await remove(todo)
    return
  }
  try {
    const result = await todoApi.move(todo.id, command === 'up' ? 'up' : 'down')
    if (!result.moved) {
      ElMessage.info(result.reason || '已经到边界了')
      return
    }
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '移动失败')
  }
}

const pageActive = ref(true)

watch(
  () => route.query,
  () => {
    // keep-alive 下其他页面的查询变化也会走到这里，隐藏时不能跟着重拉
    if (!pageActive.value) {
      return
    }
    hydrateFromQuery()
    void load()
  },
)

let activatedOnce = false
onActivated(() => {
  pageActive.value = true
  if (activatedOnce) {
    hydrateFromQuery()
    void load()
  }
  activatedOnce = true
})

onDeactivated(() => {
  pageActive.value = false
})

onMounted(async () => {
  hydrateFromQuery()
  await load()
})
</script>

<style scoped>
.todo-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 100%;
}

.todo-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.todo-stat {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: var(--aitp-panel);
  border: 1px solid var(--aitp-border);
  border-radius: var(--aitp-radius);
  box-shadow: var(--aitp-shadow);
}

.todo-stat__label {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.todo-stat__value {
  font-size: 16px;
  font-weight: 650;
}

.todo-stat.is-danger .todo-stat__value {
  color: var(--el-color-danger);
}

.todo-stat--wide {
  flex: 1;
  min-width: 220px;
}

.todo-stat__bar {
  flex: 1;
  min-width: 80px;
}

.todo-layout {
  display: grid;
  grid-template-columns: 208px minmax(0, 1fr) 320px;
  gap: 12px;
  align-items: start;
}

.todo-rail__title {
  font-size: 12px;
  font-weight: 600;
  color: var(--aitp-text-weak);
  margin: 10px 0 6px;
}

.todo-rail__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.todo-rail__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}

.todo-rail__item:hover {
  background: var(--el-fill-color-light);
}

.todo-rail__item.is-active {
  background: var(--aitp-primary-soft);
  color: var(--aitp-primary);
  font-weight: 600;
}

.todo-rail__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.todo-rail__count {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.todo-quick {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.todo-quick__field {
  width: 110px;
  flex-shrink: 0;
}

.todo-quick__date {
  width: 160px;
  flex-shrink: 0;
}

.todo-batch {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  padding: 8px 12px;
  font-size: 13px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.todo-list {
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  overflow: hidden;
}

.todo-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.todo-row:last-child {
  border-bottom: none;
}

.todo-row:hover {
  background: var(--aitp-hover);
}

.todo-row.is-active {
  background: var(--aitp-primary-soft);
}

.todo-row__bar {
  width: 3px;
  height: 16px;
  border-radius: 2px;
  flex-shrink: 0;
}

.todo-row__title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.todo-row.is-done .todo-row__title {
  text-decoration: line-through;
  color: var(--aitp-text-weak);
}

.todo-row.is-cancelled .todo-row__title {
  color: var(--aitp-text-weak);
}

.todo-row__sub,
.todo-row__progress {
  font-size: 12px;
  flex-shrink: 0;
}

.todo-children {
  background: var(--aitp-hover);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.todo-row--child {
  padding-left: 34px;
  border-bottom: none;
}

.todo-children__toggle {
  margin: 4px 0 8px 34px;
}

.todo-pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.todo-detail {
  position: sticky;
  top: 12px;
}

.todo-detail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.todo-detail__section {
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 10px;
  margin-top: 4px;
}

.todo-detail__label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}

.todo-detail__child {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}

.todo-detail__child span {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.todo-detail__child span.is-done {
  text-decoration: line-through;
  color: var(--aitp-text-weak);
}

.todo-detail__actions {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}

@media (max-width: 1280px) {
  .todo-layout {
    grid-template-columns: 190px minmax(0, 1fr);
  }

  .todo-detail {
    grid-column: 1 / -1;
    position: static;
  }
}

@media (max-width: 900px) {
  .todo-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
