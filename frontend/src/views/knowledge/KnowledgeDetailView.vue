<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">知识详情</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Back" @click="router.back()">返回</el-button>
        <el-button :icon="DocumentCopy" @click="router.push(`/knowledge/${id}/versions`)">版本记录</el-button>
        <el-button :icon="Edit" @click="editing = !editing">编辑</el-button>
        <el-button v-if="detail?.reviewStatus === 'published'" type="warning" :icon="Remove" @click="revoke">
          撤销
        </el-button>
      </div>
    </div>

    <el-skeleton v-if="loading" :rows="6" animated />

    <div v-else-if="detail" class="knowledge-detail">
      <div class="panel panel-pad">
        <div class="knowledge-detail__head">
          <h3>{{ detail.title }}</h3>
          <div>
            <el-tag :type="detail.reviewStatus === 'published' ? 'success' : 'info'" size="small" effect="plain">
              {{ detail.reviewStatus === 'published' ? '已发布' : '已撤销' }}
            </el-tag>
            <el-tag size="small" effect="plain">v{{ detail.currentVersion ?? 1 }}</el-tag>
          </div>
        </div>
        <el-descriptions :column="4" border size="small">
          <el-descriptions-item label="分类">{{ detail.category || '-' }}</el-descriptions-item>
          <el-descriptions-item label="标签">{{ detail.tags || '-' }}</el-descriptions-item>
          <el-descriptions-item label="功能点">{{ detail.moduleId ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="文件">{{ detail.filePath || '未落盘' }}</el-descriptions-item>
          <el-descriptions-item label="摘要" :span="4">{{ detail.summary || '-' }}</el-descriptions-item>
        </el-descriptions>
      </div>

      <div class="panel panel-pad">
        <template v-if="!editing">
          <MarkdownView :source="detail.content" />
        </template>
        <template v-else>
          <el-form :model="form" label-width="70px">
            <el-form-item label="标题">
              <el-input v-model="form.title" />
            </el-form-item>
            <el-form-item label="摘要">
              <el-input v-model="form.summary" type="textarea" :rows="2" />
            </el-form-item>
            <el-form-item label="正文">
              <el-input v-model="form.content" type="textarea" :rows="16" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="saving" @click="save">提交新版本</el-button>
              <el-button @click="editing = false">取消</el-button>
            </el-form-item>
          </el-form>
          <el-alert
            type="info"
            show-icon
            :closable="false"
            title="保存后产生新版本并进入待审核；当前已发布版本保持不变，仍可被检索。"
          />
        </template>
      </div>

      <div class="panel panel-pad">
        <div class="section-title">引用清单</div>
        <el-table :data="refs" size="small" border empty-text="暂无下游引用">
          <el-table-column prop="refType" label="引用类型" width="130" />
          <el-table-column prop="label" label="引用来源" min-width="220" />
          <el-table-column prop="refId" label="标识" width="100" />
          <el-table-column prop="createdAt" label="时间" width="170" />
        </el-table>
      </div>
    </div>

    <el-empty v-else description="知识条目不存在" />

    <ReferenceBlockDialog
      v-model="blockedVisible"
      :message="blockedMessage"
      :refs="blockedRefs"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, DocumentCopy, Edit, Remove } from '@element-plus/icons-vue'
import MarkdownView from '@/components/MarkdownView.vue'
import { knowledgeApi } from '@/api'
import type { KnowledgeArticle, KnowledgeRef } from '@/api/types'
import { useReferenceGuard } from '@/composables/useReferenceGuard'
import ReferenceBlockDialog from '@/components/ReferenceBlockDialog.vue'

const route = useRoute()
const router = useRouter()
const id = computed(() => Number(route.params.id))

const detail = ref<KnowledgeArticle | null>(null)
const refs = ref<KnowledgeRef[]>([])
const loading = ref(true)
const saving = ref(false)
const editing = ref(false)

const form = reactive({ title: '', content: '', summary: '' })

const { blockedVisible, blockedRefs, blockedMessage, handleError } = useReferenceGuard()

async function load(): Promise<void> {
  try {
    const payload = await knowledgeApi.getArticle(id.value)
    detail.value = payload
    Object.assign(form, {
      title: payload.title ?? '',
      content: payload.content ?? '',
      summary: payload.summary ?? '',
    })
    refs.value = await knowledgeApi.listRefs(id.value)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  try {
    await knowledgeApi.updateArticle(id.value, { ...form })
    ElMessage.success('已提交新版本，待审核通过后生效')
    editing.value = false
    await load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function revoke(): Promise<void> {
  try {
    await ElMessageBox.confirm('撤销后条目将移出数字资产与全文检索。', '撤销确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await knowledgeApi.revokeArticle(id.value)
    ElMessage.success('已撤销')
    await load()
  } catch (err) {
    handleError(err, '撤销失败')
  }
}

onMounted(() => {
  void load()
})
</script>

<style scoped>
.knowledge-detail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.knowledge-detail__head h3 {
  margin: 0;
  font-size: 17px;
}

.section-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 8px;
}
</style>
