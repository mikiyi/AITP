<template>
  <el-tour
    v-model="open"
    :current="current"
    :show-arrow="true"
    :mask="true"
    :scroll-into-view-options="true"
    @update:current="onChange"
    @finish="onFinish"
    @close="onSkip"
  >
    <el-tour-step
      v-for="step in STEPS"
      :key="step.title"
      :target="step.target"
      :title="step.title"
    >
      <template #default>
        <!-- 步骤正文按 index 渲染，保证每个 target 只在自己那步出现 -->
        <template v-if="current === STEPS.indexOf(step)">
          <span v-html="step.body" />
          <div v-if="step.action" style="margin-top: 8px">
            <el-button
              size="small"
              :type="step.action.type || 'default'"
              :loading="step.action.loading === 'injecting' ? injecting : false"
              @click="step.action.run"
            >
              {{ step.action.label }}
            </el-button>
          </div>
        </template>
      </template>
    </el-tour-step>
  </el-tour>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { onboardingApi, projectApi } from '@/api'

const emit = defineEmits<{ (e: 'done'): void }>()
const router = useRouter()

const open = ref(false)
const current = ref(0)
const injecting = ref(false)
const demoLoaded = ref(false)

/**
 * 跨页引导：每步先跳到对应页面，再高亮该页真实存在的元素。
 * target 是各目标页自己的选择器（不是全程钉在首页），
 * onChange 里做路由跳转 + 等 DOM 渲染，避免「文案说去别的页、框却钉在首页」。
 */
interface Step {
  title: string
  body: string
  route: string
  target: string
  action?: { label: string; run: () => void; type?: 'primary' | 'default'; loading?: 'injecting' }
}

const STEPS = computed<Step[]>(() => [
  {
    title: '第一步：配置 AI 模型',
    body: '用例生成、缺陷分析、知识库问答等 AI 能力都要先配好模型。<br>在「AI 设置」页添加供应商档案并填 Key；本地 Ollama 不需要 Key。<br>这一步现在跳过也可以，示例项目的浏览不依赖模型。',
    route: '/settings/ai',
    target: '.ai-cost-summary',
    action: { label: '已了解，下一步', run: () => next() },
  },
  {
    title: '第二步：载入示例项目',
    body: '一键注入示例电商项目 DEMO-EC：需求 / 用例 / 缺陷 / 知识 / 报告，外加一个可直接执行的接口断言脚本。<br>只追加示例自身，绝不动你已有的数据。也可以在「项目」列表空态一键载入。',
    route: '/projects',
    target: '.project-empty',
    action: { label: demoLoaded.value ? '示例已载入（点此重建）' : '载入示例项目', run: injectDemo, type: 'primary', loading: 'injecting' },
  },
  {
    title: '第三步：跑一次 AI 生成',
    body: '进入「AI 实验室」，点下面的「用例生成」能力卡，填一段需求原文再点「执行」，就能看到 AI 产出的结构化用例；<br>产出点「采纳为用例」才会进正式用例库，采纳时会自动标出疑似重复。',
    route: '/lab',
    target: '.lab-card',
    action: { label: '已了解，下一步', run: () => next() },
  },
  {
    title: '第四步：看报告与追溯',
    body: '执行用例后到「测试报告」看汇总，报告里的失败样本可点「查看证据」下钻到断言明细与附件；「追溯与准入」能从需求一路排到用例结果与未闭环缺陷。<br>引导到此结束，随时可在「使用帮助」里重新打开。',
    route: '/reports',
    target: '.panel',
    action: { label: '完成引导', run: () => finish() },
  },
])

function next(): void {
  if (current.value < STEPS.value.length - 1) {
    current.value += 1
  }
}

function finish(): void {
  open.value = false
}

/** 步骤变化时推进 current，再跳路由等目标元素出现，让高亮框落在正确的页面上。 */
async function onChange(index: number): Promise<void> {
  const clamped = Math.max(0, Math.min(index, STEPS.value.length - 1))
  if (clamped === current.value) return
  current.value = clamped
  const step = STEPS.value[clamped]
  if (router.currentRoute.value.path !== step.route) {
    await router.push(step.route)
  }
  await waitFor(step.target)
}

function waitFor(selector: string, tries = 40): Promise<void> {
  return new Promise((resolve) => {
    let n = 0
    const tick = async (): Promise<void> => {
      if (document.querySelector(selector)) return resolve()
      if (++n >= tries) return resolve()
      await new Promise((r) => setTimeout(r, 100))
      void tick()
    }
    void tick()
  })
}

/** 打开引导：先落到第一步的目标页（AI 设置），再弹出，避免第一步悬空在别的页面。 */
async function startTour(): Promise<void> {
  current.value = 0
  const first = STEPS.value[0]
  if (router.currentRoute.value.path !== first.route) {
    await router.push(first.route)
  }
  await waitFor(first.target)
  open.value = true
}

onMounted(async () => {
  try {
    const state = await onboardingApi.state()
    const projects = await projectApi.list({ pageSize: 100 })
    demoLoaded.value = projects.list.some((item) => item.code === 'DEMO-EC')
    if (state.showGuide) {
      await startTour()
    }
  } catch {
    // 状态拉取失败不阻塞页面
  }
})

async function settle(action: 'complete' | 'skip'): Promise<void> {
  open.value = false
  try {
    await onboardingApi.update(action)
  } catch {
    // 状态写失败下次仍会弹，可用帮助页重开
  }
  emit('done')
}

function onFinish(): void {
  void settle('complete')
}

function onSkip(): void {
  void settle('skip')
}

async function injectDemo(): Promise<void> {
  injecting.value = true
  try {
    const result = await onboardingApi.injectDemo()
    demoLoaded.value = true
    ElMessage.success(
      `示例项目已载入：${Object.values(result.counts).reduce((sum, n) => sum + n, 0)} 条演示数据`,
    )
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '示例注入失败')
  } finally {
    injecting.value = false
  }
}

/** 帮助页「重新打开引导」入口：重置到第一步（落在 AI 设置页）并弹出。 */
async function reopen(): Promise<void> {
  try {
    await onboardingApi.update('reopen')
  } catch {
    // 忽略，本地仍会弹出
  }
  await startTour()
}

defineExpose({ reopen })

// 路由被用户手动切走时同步 current，避免高亮停在已被卸载的元素上
watch(
  () => router.currentRoute.value.path,
  () => {
    if (open.value) void waitFor(STEPS.value[current.value]?.target ?? '.page-header')
  },
)
</script>
