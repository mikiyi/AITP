<template>
  <el-tour
    v-model="open"
    :show-arrow="true"
    :mask="true"
    :scroll-into-view-options="true"
    @finish="onFinish"
    @close="onSkip"
  >
    <el-tour-step :target="'.home .page-header'" title="第一步：配置 AI 模型">
      <template #default>
        AI 能力（用例生成、缺陷分析、知识库问答等）需要先配置模型。<br>
        去「设置 → AI 设置」添加供应商档案并填 Key；本地 Ollama 不需要 Key。<br>
        这一步现在跳过也可以，示例项目的浏览不依赖模型。<br>
        <el-button size="small" style="margin-top: 8px" @click="goAiSettings">去配置模型</el-button>
      </template>
    </el-tour-step>
    <el-tour-step :target="'.home .home__stats'" title="第二步：载入示例项目">
      <template #default>
        一键注入示例电商项目（DEMO-EC）：3 个需求、12 条用例、5 个缺陷、<br>
        6 篇知识与 1 个可直接执行的接口自动化脚本。只追加示例，绝不动你已有的数据。<br>
        <el-button size="small" type="primary" style="margin-top: 8px" :loading="injecting" @click="injectDemo">
          {{ demoLoaded ? '示例已载入（点此重建）' : '载入示例项目' }}
        </el-button>
      </template>
    </el-tour-step>
    <el-tour-step :target="'.home .home__section'" title="第三步：跑一次 AI 生成">
      <template #default>
        进入「测试功能点」选中示例项目的功能点，点「生成用例」即可看到 AI 产出；<br>
        产出先进待审池，采纳后才进正式用例库。
      </template>
    </el-tour-step>
    <el-tour-step :target="'.home .page-header'" title="第四步：看报告与追溯">
      <template #default>
        执行用例后去「测试报告」查看汇总，追溯矩阵可下钻到需求与缺陷。<br>
        引导到此结束，随时可在「使用帮助」里重新打开。
      </template>
    </el-tour-step>
  </el-tour>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { onboardingApi, projectApi } from '@/api'

const emit = defineEmits<{ (e: 'done'): void }>()
const router = useRouter()

const open = ref(false)
const injecting = ref(false)
const demoLoaded = ref(false)

onMounted(async () => {
  try {
    const state = await onboardingApi.state()
    if (state.showGuide) {
      open.value = true
    }
    const projects = await projectApi.list({ pageSize: 100 })
    demoLoaded.value = projects.list.some((item) => item.code === 'DEMO-EC')
  } catch {
    // 状态拉取失败不阻塞首页
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

function goAiSettings(): void {
  router.push('/settings/ai')
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

/** 帮助页「重新打开引导」入口：重置状态并弹出。 */
async function reopen(): Promise<void> {
  try {
    await onboardingApi.update('reopen')
  } catch {
    // 忽略，本地仍会弹出
  }
  open.value = true
}

defineExpose({ reopen })
</script>
