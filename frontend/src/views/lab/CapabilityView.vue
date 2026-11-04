<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">
        {{ spec?.label ?? capability }}
        <el-tag v-if="spec" size="small" effect="plain" class="cap-tag">{{ spec.goal }}</el-tag>
      </h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Back" @click="router.push('/lab')">返回实验室</el-button>
      </div>
    </div>

    <p v-if="spec" class="cap-sub">
      <span class="cap-sub__prompt">{{ spec.promptCode }}</span>
      <span class="cap-sub__desc">{{ spec.promptDescription || spec.goal }}</span>
    </p>

    <div class="cap-layout">
      <div class="panel panel-pad cap-input">
        <el-form label-width="120px" label-position="right">
          <el-form-item label="功能点">
            <el-select v-model="moduleId" clearable filterable placeholder="选择功能点（可选）">
              <el-option
                v-for="item in moduleOptions"
                :key="item.id"
                :label="item.path"
                :value="item.id"
              />
            </el-select>
          </el-form-item>

          <el-form-item v-for="field in fields" :key="field.name">
            <template #label>
              <span class="cap-field-label">
                <span class="cap-field-label__text">{{ field.label }}</span>
                <el-tag
                  v-if="isOptionalVariable(field.name)"
                  size="small"
                  type="info"
                  effect="plain"
                  class="cap-field-optional"
                >
                  选填
                </el-tag>
              </span>
            </template>
            <el-select
              v-if="field.type === 'select'"
              v-model="values[field.name]"
              class="cap-field-select"
              :placeholder="field.placeholder ?? '请选择'"
            >
              <el-option
                v-for="option in visibleOptions(field, values)"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
            <el-select
              v-else-if="field.type === 'multiselect'"
              v-model="multiValues[field.name]"
              class="cap-field-select"
              multiple
              collapse-tags
              collapse-tags-tooltip
              :placeholder="field.placeholder ?? '可多选'"
            >
              <el-option
                v-for="option in visibleOptions(field, values)"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
            <el-input
              v-else-if="field.type === 'textarea'"
              v-model="values[field.name]"
              type="textarea"
              :rows="field.rows ?? 6"
              :placeholder="field.placeholder"
            />
            <el-input
              v-else-if="field.type === 'number'"
              v-model="values[field.name]"
              type="number"
              :placeholder="field.placeholder"
            />
            <el-input v-else v-model="values[field.name]" :placeholder="field.placeholder" />
          </el-form-item>

          <el-form-item v-if="capability === 'xmind_to_case'" label="XMind 文件">
            <el-upload
              :show-file-list="false"
              :http-request="uploadXmind"
              accept=".xmind"
            >
              <el-button :icon="Upload" :loading="parsing">上传 .xmind</el-button>
            </el-upload>
            <el-button link type="primary" :loading="parsing" @click="parseOutlineText">
              解析粘贴的大纲
            </el-button>
          </el-form-item>

          <el-form-item label="附件">
            <div class="cap-attachments">
              <div class="cap-attachments__actions">
                <el-upload
                  :show-file-list="false"
                  :http-request="uploadDocumentFile"
                  :accept="DOC_ACCEPT"
                >
                  <el-button :icon="Document" :loading="importing">导入文档</el-button>
                </el-upload>
                <el-upload
                  :show-file-list="false"
                  :http-request="uploadImageFile"
                  :accept="IMAGE_ACCEPT"
                >
                  <el-button :icon="Picture" :loading="uploadingImage">上传图片</el-button>
                </el-upload>
                <span class="cap-attachments__hint">也可直接 Ctrl+V 粘贴图片</span>
              </div>

              <div v-if="documents.length" class="cap-attachments__docs">
                <el-tag
                  v-for="item in documents"
                  :key="item.attachmentId"
                  size="small"
                  type="info"
                  effect="plain"
                  class="cap-attachments__doc"
                >
                  {{ item.fileName }}（{{ item.chars }} 字）
                </el-tag>
              </div>

              <div v-if="images.length" class="cap-attachments__list">
                <div v-for="item in images" :key="item.attachmentId" class="cap-attachment">
                  <img :src="item.previewUrl" class="cap-attachment__thumb" alt="附件预览" />
                  <div class="cap-attachment__meta">
                    <span class="cap-attachment__name">{{ item.fileName }}</span>
                    <el-tag
                      size="small"
                      :type="item.watermarkApplied ? 'success' : 'info'"
                      effect="plain"
                    >
                      {{ item.watermarkApplied ? '已去水印' : '未发现水印' }}
                    </el-tag>
                  </div>
                  <el-button link type="danger" @click="removeImage(item.attachmentId)">移除</el-button>
                </div>
              </div>
            </div>
          </el-form-item>

          <el-form-item v-if="templateKind" label="产出物模板">
            <div class="cap-template">
              <el-switch v-model="useTemplate" @change="onTemplateToggle" />
              <template v-if="useTemplate">
                <el-select
                  v-model="templateId"
                  class="cap-template__select"
                  placeholder="选择同类模板"
                  :loading="templateLoading"
                >
                  <el-option
                    v-for="item in templateOptions"
                    :key="item.id"
                    :label="item.name"
                    :value="item.id"
                  >
                    <span>{{ item.name }}</span>
                    <span class="cap-template__kind">{{ item.kindLabel }}</span>
                  </el-option>
                </el-select>
                <el-button link type="primary" @click="router.push('/settings/templates')">
                  管理模板
                </el-button>
              </template>
              <span class="cap-template__hint">
                开启后 AI 按模板的文档风格、格式与字号输出，产出可直接导出
              </span>
            </div>
          </el-form-item>

          <PromptOptimizer
            v-model="values[primaryField]"
            :capability="capability"
          />

          <el-form-item>
            <div class="cap-actions">
              <el-button
                type="primary"
                :icon="Promotion"
                :loading="running"
                @click="run"
              >
                执行
              </el-button>
              <span class="cap-actions__hint">执行结果在右侧产出区，复制 / 采纳 / 入库都在那边</span>
            </div>
          </el-form-item>
        </el-form>

        <el-alert
          v-if="egressWarning"
          type="warning"
          :closable="false"
          show-icon
          :title="egressWarning"
          class="egress-alert"
        />
        <el-alert
          v-if="reviewRequired"
          type="warning"
          :closable="false"
          show-icon
          title="【需人工复核】结论含 P0/P1 或提示词测试结果，采纳前请人工确认"
          class="egress-alert"
        />
      </div>

      <div class="panel panel-pad cap-output">
        <div class="cap-output__head">
          <el-radio-group v-model="outputTab" size="small">
            <el-radio-button value="current">本次产出</el-radio-button>
            <el-radio-button value="history">历史产出（{{ historyTotal }}）</el-radio-button>
          </el-radio-group>

          <template v-if="outputTab === 'current'">
            <span v-if="model" class="muted">模型：{{ model }}</span>
            <span v-if="latencyMs" class="muted">耗时 {{ latencyMs }} ms</span>
            <el-button v-if="reasoning" link type="primary" @click="reasoningVisible = true">
              查看推理过程
            </el-button>
          </template>
          <template v-else>
            <span class="muted">每次执行的原文都留在这里，可回看、复制、下载</span>
            <el-button link :icon="Refresh" :loading="historyLoading" @click="loadHistory(historyPage)">
              刷新
            </el-button>
          </template>
        </div>

        <!-- 产出物操作：都是对本次产出的处理，放在产出区而不是输入表单里 -->
        <div v-if="outputTab === 'current'" class="cap-output__actions">
          <el-button :icon="CopyDocument" :disabled="!output" @click="copyOutput">复制</el-button>
          <el-button :icon="Download" :disabled="!output" @click="download">下载</el-button>
          <el-button
            v-if="excelCapable"
            :icon="Grid"
            :loading="exportingExcel"
            :disabled="!finished || !output"
            @click="exportExcel"
          >
            导出 Excel
          </el-button>
          <el-button
            v-if="wordCapable"
            :icon="Document"
            :loading="exportingWord"
            :disabled="!finished || !output"
            @click="exportWord"
          >
            导出 Word
          </el-button>
          <el-button :icon="Collection" :disabled="!finished || !output" @click="saveToKnowledge">
            加入知识库
          </el-button>
          <el-button :icon="DocumentAdd" :disabled="!finished || !output" @click="openAdoptAsset('requirement')">
            添加需求
          </el-button>
          <el-button
            :icon="Warning"
            type="warning"
            plain
            :disabled="!finished || !output"
            @click="openAdoptAsset('bug')"
          >
            添加缺陷
          </el-button>
          <el-button :icon="Refresh" :disabled="!finished || !output" @click="openAdoptAsset('regression')">
            添加回归清单
          </el-button>
          <el-button
            v-if="capability === 'sql_analysis'"
            :icon="DataLine"
            :disabled="!finished || !output"
            @click="openAdoptAsset('sql')"
          >
            添加 SQL 分析
          </el-button>
          <el-button
            v-if="spec?.adoptable"
            type="success"
            :icon="Select"
            :disabled="!finished || !output"
            @click="openAdopt"
          >
            采纳为用例
          </el-button>
          <el-button
            v-if="capability === 'report_generation'"
            type="success"
            :icon="Select"
            :disabled="!finished || !output"
            @click="openAdoptReport"
          >
            采纳为报告
          </el-button>
          <el-button
            v-if="capability === 'performance_script'"
            type="success"
            :icon="Odometer"
            :loading="importingScript"
            :disabled="!finished || !output"
            @click="importToAutomation('performance')"
          >
            加入性能测试
          </el-button>
          <el-button
            v-if="capability === 'automation_script'"
            type="success"
            :icon="FolderAdd"
            :loading="importingScript"
            :disabled="!finished || !output"
            @click="importToAutomation(automationCategory)"
          >
            加入{{ automationCategoryLabel }}自动化
          </el-button>
        </div>

        <!-- 本次产出 -->
        <template v-if="outputTab === 'current'">
          <div v-if="running" class="cap-output__loading">
            <el-icon class="is-loading"><Loading /></el-icon>
            <span>模型生成中…</span>
          </div>

          <template v-if="!running">
            <div v-if="excelArtifact" class="cap-artifact">
              <el-icon :size="18" color="var(--el-color-success)"><Document /></el-icon>
              <div class="cap-artifact__body">
                <span class="cap-artifact__title">Excel 产出已生成</span>
                <span class="cap-artifact__meta">
                  {{ excelArtifact.filename }}
                  <template v-if="excelArtifact.count"> · {{ excelArtifact.count }} 条</template>
                </span>
              </div>
              <el-button type="success" size="small" :icon="Download" @click="downloadExcelArtifact">
                下载
              </el-button>
            </div>

            <MindMap
              v-if="mindNodes.length"
              :nodes="mindNodes"
              :height="460"
              @node-context="onNodeContext"
            />
            <el-alert
              v-if="mindNodes.length && (capability === 'xmind_to_case' || capability === 'testpoint_generation')"
              type="info"
              :closable="false"
              show-icon
              title="右键脑图节点可按该节点单独生成用例"
              class="mindmap-hint"
            />
            <MarkdownView v-else-if="output && !isJson" :source="output" />
            <pre v-else-if="output" class="cap-output__json">{{ prettyJson }}</pre>
            <el-empty v-if="!output && !mindNodes.length" :description="emptyDescription" :image-size="80" />
          </template>

          <div v-if="refs.length" class="cap-refs">
            <span class="cap-refs__title">参考来源</span>
            <el-tag
              v-for="ref in refs"
              :key="ref.marker"
              class="cap-refs__item"
              type="info"
              effect="plain"
              @click="router.push(`/knowledge/${ref.articleId}`)"
            >
              [{{ ref.marker }}] {{ ref.title }}
            </el-tag>
          </div>
        </template>

        <!-- 历史产出：左侧列表 + 右侧原文 -->
        <div v-else class="cap-history">
          <div class="cap-history__side">
            <div v-if="history.length" class="cap-history__list">
              <button
                v-for="item in history"
                :key="item.id"
                type="button"
                class="cap-history__item"
                :class="{ 'is-active': historyDetail?.id === item.id }"
                @click="openHistory(item)"
              >
                <div class="cap-history__item-top">
                  <span class="cap-history__time">{{ formatRecordTime(item.createdAt) }}</span>
                  <el-tag size="small" :type="reviewTagType(item)" effect="plain">
                    {{ reviewText(item) }}
                  </el-tag>
                </div>
                <div class="cap-history__summary">{{ item.summary || '（无摘要）' }}</div>
                <div class="cap-history__meta">
                  <span>{{ item.model || '本地规则' }}</span>
                  <span v-if="item.latencyMs">{{ item.latencyMs }} ms</span>
                </div>
              </button>
            </div>
            <el-empty
              v-else-if="!historyLoading"
              description="还没有历史产出，执行一次后会出现在这里"
              :image-size="64"
            />

            <el-pagination
              v-if="historyTotal > HISTORY_PAGE_SIZE"
              small
              layout="prev, pager, next"
              :total="historyTotal"
              :page-size="HISTORY_PAGE_SIZE"
              :current-page="historyPage"
              class="cap-history__pager"
              @current-change="loadHistory"
            />
          </div>

          <div class="cap-history__view">
            <template v-if="historyDetail">
              <div class="cap-history__view-head">
                <div class="cap-history__view-meta">
                  <el-tag size="small" type="warning" effect="plain">历史记录</el-tag>
                  <span class="muted">{{ formatRecordTime(historyDetail.createdAt) }}</span>
                  <span v-if="historyDetail.model" class="muted">模型：{{ historyDetail.model }}</span>
                  <span v-if="historyDetail.latencyMs" class="muted">耗时 {{ historyDetail.latencyMs }} ms</span>
                </div>
                <div class="cap-history__view-actions">
                  <el-button link :icon="CopyDocument" @click="copyHistory">复制</el-button>
                  <el-button link :icon="Download" @click="downloadHistory">下载</el-button>
                  <el-button link type="primary" @click="router.push(`/lab/records/${historyDetail.id}`)">
                    执行详情
                  </el-button>
                </div>
              </div>

              <MarkdownView
                v-if="historyDetail.outputText && !historyIsJson"
                :source="historyDetail.outputText"
              />
              <pre v-else-if="historyDetail.outputText" class="cap-output__json">{{ prettyHistoryJson }}</pre>
              <el-empty
                v-else
                description="这条记录没有保存产出原文（旧版本记录），可查看执行详情"
                :image-size="80"
              />
            </template>
            <el-empty v-else description="选择左侧一条历史产出查看原文" :image-size="80" />
          </div>
        </div>
      </div>
    </div>

    <el-drawer v-model="reasoningVisible" title="推理过程" size="40%">
      <pre class="cap-output__json">{{ reasoning }}</pre>
    </el-drawer>

    <el-dialog
      v-model="adoptAssetVisible"
      :title="`添加为${ASSET_LABEL[adoptAssetTarget]}`"
      width="520px"
    >
      <el-form label-width="80px">
        <el-form-item label="项目" required>
          <el-select v-model="adoptAssetForm.projectId" placeholder="选择项目">
            <el-option
              v-for="item in appStore.projects"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" required>
          <el-input v-model="adoptAssetForm.title" maxlength="200" />
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="产出正文会完整写入这条记录，可在对应列表继续编辑；导航栏会出现未读提醒"
      />
      <template #footer>
        <el-button @click="adoptAssetVisible = false">取消</el-button>
        <el-button type="primary" :loading="adoptingAsset" @click="confirmAdoptAsset">
          确认添加
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="knowledgeVisible" title="加入知识库" width="560px">
      <el-form label-width="80px">
        <el-form-item label="标题" required>
          <el-input v-model="knowledgeForm.title" />
        </el-form-item>
        <el-form-item label="分类">
          <el-select v-model="knowledgeForm.category">
            <el-option
              v-for="item in categoryOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="knowledgeForm.tags" placeholder="逗号分隔" />
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="保存后进入待审核池，审核通过才计入数字资产"
      />
      <template #footer>
        <el-button @click="knowledgeVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingKnowledge" @click="confirmSaveKnowledge">保存</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="adoptVisible" title="采纳为测试用例" width="720px">
      <el-form label-width="80px">
        <el-form-item label="项目" required>
          <el-select v-model="adoptForm.projectId" placeholder="选择项目">
            <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="需求">
          <el-select v-model="adoptForm.requirementId" placeholder="选择需求（可选）" clearable filterable>
            <el-option v-for="item in requirementOptions" :key="item.id" :label="item.title" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="功能点">
          <el-select v-model="adoptForm.moduleId" placeholder="选择功能点（可选）" clearable filterable>
            <el-option v-for="item in moduleOptions" :key="item.id" :label="item.path" :value="item.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <el-table :data="adoptRows" size="small" border max-height="320">
        <el-table-column prop="title" label="用例标题" min-width="240" show-overflow-tooltip />
        <el-table-column prop="caseType" label="类型" width="100" />
        <el-table-column prop="priority" label="优先级" width="90" />
        <el-table-column label="步骤数" width="80" align="right">
          <template #default="scope">{{ scope.row.steps?.length ?? 0 }}</template>
        </el-table-column>
      </el-table>
      <el-alert
        v-if="adoptRows.length"
        type="warning"
        :closable="false"
        show-icon
        class="adopt-alert"
        title="AI 生成的用例需人工复核后再执行；采纳后状态为「未执行」"
      />
      <el-empty v-if="!adoptRows.length" description="产出不是用例 JSON 数组，无法自动采纳" :image-size="80" />
      <template #footer>
        <el-button @click="adoptVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="adopting"
          :disabled="!adoptRows.length"
          @click="confirmAdopt"
        >
          采纳 {{ adoptRows.length }} 条
        </el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="reportAdoptVisible" title="采纳为测试报告" width="560px">
      <el-form label-width="90px">
        <el-form-item label="项目" required>
          <el-select v-model="reportForm.projectId" placeholder="选择项目">
            <el-option v-for="item in appStore.projects" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" required>
          <el-input v-model="reportForm.title" />
        </el-form-item>
        <el-form-item label="报告类型">
          <el-select v-model="reportForm.reportType">
            <el-option
              v-for="item in reportTypeOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="统计周期">
          <el-date-picker
            v-model="reportPeriod"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始"
            end-placeholder="结束"
          />
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="AI 产出的二级标题会拆成报告章节；采纳后为草稿，发布前请人工复核"
      />
      <template #footer>
        <el-button @click="reportAdoptVisible = false">取消</el-button>
        <el-button type="primary" :loading="reportAdopting" @click="confirmAdoptReport">采纳</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import {
  Back,
  Collection,
  CopyDocument,
  Document,
  DataLine,
  DocumentAdd,
  Download,
  FolderAdd,
  Grid,
  Loading,
  Odometer,
  Picture,
  Promotion,
  Refresh,
  Select,
  Upload,
  Warning,
} from '@element-plus/icons-vue'
import MarkdownView from '@/components/MarkdownView.vue'
import MindMap from '@/components/MindMap.vue'
import PromptOptimizer from '@/components/PromptOptimizer.vue'
import { automationApi, caseApi, knowledgeApi, labApi, recordApi, reportApi, requirementApi, templateApi } from '@/api'
import { streamPost, type DegradeInfo } from '@/api/stream'
import type {
  AdoptCaseItem,
  AiRecord,
  DocTemplate,
  DocumentAttachment,
  ImageAttachment,
  KnowledgeRef,
  MindNode,
  Requirement,
} from '@/api/types'
import { useAppStore } from '@/stores/app'
import { useAiGuardStore } from '@/stores/aiGuard'
import { useBadgeStore } from '@/stores/badges'
import { DEFAULT_KNOWLEDGE_CATEGORY, useKnowledgeCategoryStore } from '@/stores/knowledgeCategory'
import { useLabStore } from '@/stores/lab'
import { CAPABILITY_FORMS, optionPromptText, primaryFieldOf, visibleFields, visibleOptions } from './capabilityForms'
import { findNodeByPath, subtreeText, testpointToMindNodes } from './mindmap'

const route = useRoute()
const router = useRouter()
const labStore = useLabStore()
const appStore = useAppStore()
const categoryStore = useKnowledgeCategoryStore()
const badgeStore = useBadgeStore()
const aiGuard = useAiGuardStore()

const capability = computed(() => String(route.params.capability ?? ''))
const spec = computed(
  () =>
    labStore.allCapabilities.find((item) => item.code === capability.value) ??
    labStore.capabilities.find((item) => item.code === capability.value) ??
    null,
)
const fields = computed(() => visibleFields(capability.value, values))
const moduleOptions = computed(() => labStore.moduleOptions)
const primaryField = computed(() => primaryFieldOf(capability.value))

const values = reactive<Record<string, string>>({})
/** 多选字段的选中项，单独存一份数组，提交时再拼成字符串。 */
const multiValues = reactive<Record<string, string[]>>({})
const moduleId = ref<number | null>(null)

const running = ref(false)
const output = ref('')
const reasoning = ref('')
const reasoningVisible = ref(false)
const model = ref('')
const latencyMs = ref(0)
const egressWarning = ref('')
const reviewRequired = ref(false)
const refs = ref<KnowledgeRef[]>([])
const parsing = ref(false)
/** 本次执行是否已正常收尾；流式过程中产出可能还是半个 JSON，不允许采纳。 */
const finished = ref(false)
/** xmind 解析出来的树，用于脑图展示与按节点生成用例。 */
const xmindTree = ref<MindNode[]>([])

/* ---- 产出物模板 ---- */

/** 能力 → 可用的模板类别；空表示该能力不支持模板。 */
const TEMPLATE_KIND: Record<string, 'case' | 'report' | 'mindmap'> = {
  case_generation: 'case',
  xmind_to_case: 'case',
  api_test_design: 'case',
  report_generation: 'report',
  testpoint_generation: 'mindmap',
}

/** 支持导出 Excel 的能力。 */
const EXCEL_CAPABILITIES = ['case_generation', 'xmind_to_case', 'api_test_design']
/** 支持导出 Word 的能力：用例类 + 报告。 */
const WORD_CAPABILITIES = [...EXCEL_CAPABILITIES, 'report_generation']

const templateKind = computed(() => TEMPLATE_KIND[capability.value] ?? '')
const excelCapable = computed(() => EXCEL_CAPABILITIES.includes(capability.value))
const wordCapable = computed(() => WORD_CAPABILITIES.includes(capability.value))

const useTemplate = ref(false)
const templateId = ref<number | null>(null)
const templateOptions = ref<DocTemplate[]>([])
const templateLoading = ref(false)

async function loadTemplateOptions(): Promise<void> {
  if (!templateKind.value) {
    return
  }
  templateLoading.value = true
  try {
    const result = await templateApi.options(templateKind.value)
    templateOptions.value = result.list
    if (!result.list.length) {
      ElMessage.warning('还没有该类型的测试文档模板，请先到「设置 → 测试文档模板设置」新增')
      useTemplate.value = false
      templateId.value = null
      return
    }
    const stillExists = result.list.some((item) => item.id === templateId.value)
    if (!stillExists) {
      const preferred = result.list.find((item) => item.isDefault) ?? result.list[0]
      templateId.value = preferred.id
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载产出物模板失败')
  } finally {
    templateLoading.value = false
  }
}

function onTemplateToggle(value: string | number | boolean): void {
  if (value) {
    void loadTemplateOptions()
  } else {
    templateId.value = null
  }
}

/* ---- Excel 导出 ---- */

interface ExcelArtifact {
  blob: Blob
  filename: string
  count: number | null
}

/** 本次执行生成的 Excel 产出；执行成功后自动生成，产出面板里直接可下载。 */
const excelArtifact = ref<ExcelArtifact | null>(null)
const exportingExcel = ref(false)

async function buildExcelArtifact(quiet: boolean): Promise<ExcelArtifact | null> {
  if (!excelCapable.value || !output.value.trim()) {
    return null
  }
  exportingExcel.value = true
  try {
    const result = await labApi.exportOutput({
      capability: capability.value,
      output: output.value,
      templateId: useTemplate.value && templateId.value ? templateId.value : undefined,
    })
    return {
      blob: result.blob,
      filename: result.filename || `${capability.value}.xlsx`,
      count: result.count,
    }
  } catch (err) {
    if (!quiet) {
      ElMessage.error(err instanceof Error ? err.message : '导出失败')
    }
    return null
  } finally {
    exportingExcel.value = false
  }
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

/** 手动导出：生成（或复用）Excel 后立即下载。 */
async function exportExcel(): Promise<void> {
  if (!output.value.trim()) {
    ElMessage.warning('暂无产出')
    return
  }
  const artifact = await buildExcelArtifact(false)
  if (!artifact) {
    return
  }
  excelArtifact.value = artifact
  saveBlob(artifact.blob, artifact.filename)
  ElMessage.success('已导出 Excel')
}

/** 执行成功后静默生成 Excel 产出，成品直接挂在产出面板上。 */
async function prepareExcelArtifact(): Promise<void> {
  if (!excelCapable.value) {
    return
  }
  excelArtifact.value = await buildExcelArtifact(true)
}

const exportingWord = ref(false)

/** Word 产出：按需生成并立即下载。 */
async function exportWord(): Promise<void> {
  if (!output.value.trim()) {
    ElMessage.warning('暂无产出')
    return
  }
  exportingWord.value = true
  try {
    const result = await labApi.exportOutput({
      capability: capability.value,
      output: output.value,
      templateId: useTemplate.value && templateId.value ? templateId.value : undefined,
      format: 'docx',
    })
    saveBlob(result.blob, result.filename || `${capability.value}.docx`)
    ElMessage.success('已导出 Word')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '导出失败')
  } finally {
    exportingWord.value = false
  }
}

function downloadExcelArtifact(): void {
  const artifact = excelArtifact.value
  if (artifact) {
    saveBlob(artifact.blob, artifact.filename)
  }
}

/* ---- 一键添加到资产 ---- */

type AssetTarget = 'requirement' | 'bug' | 'regression' | 'sql'

const ASSET_LABEL: Record<AssetTarget, string> = {
  requirement: '需求',
  bug: '缺陷',
  regression: '回归清单',
  sql: 'SQL 分析',
}

const adoptAssetVisible = ref(false)
const adoptAssetTarget = ref<AssetTarget>('requirement')
const adoptingAsset = ref(false)
const adoptAssetForm = reactive({
  projectId: null as number | null,
  title: '',
})

/** 默认标题取产出第一行有效内容，去掉 Markdown 标题符号。 */
function defaultAssetTitle(): string {
  const first = output.value
    .split('\n')
    .map((line) => line.trim().replace(/^#+\s*/, ''))
    .find((line) => Boolean(line))
  return (first || spec.value?.label || capability.value).slice(0, 60)
}

function openAdoptAsset(target: AssetTarget): void {
  if (!output.value.trim()) {
    ElMessage.warning('暂无产出')
    return
  }
  adoptAssetTarget.value = target
  adoptAssetForm.projectId = appStore.currentProjectId
  adoptAssetForm.title = defaultAssetTitle()
  adoptAssetVisible.value = true
}

async function confirmAdoptAsset(): Promise<void> {
  if (!adoptAssetForm.projectId) {
    ElMessage.warning('请先选择项目')
    return
  }
  if (!adoptAssetForm.title.trim()) {
    ElMessage.warning('请填写标题')
    return
  }
  adoptingAsset.value = true
  try {
    const result = await labApi.adoptOutput({
      capability: capability.value,
      output: output.value,
      target: adoptAssetTarget.value,
      projectId: adoptAssetForm.projectId,
      title: adoptAssetForm.title.trim(),
      moduleId: moduleId.value ?? undefined,
    })
    ElMessage.success(`已添加到${ASSET_LABEL[adoptAssetTarget.value]}：${result.title}`)
    adoptAssetVisible.value = false
    await badgeStore.load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '添加失败')
  } finally {
    adoptingAsset.value = false
  }
}

/* ---- 历史产出 ---- */

/** 右侧面板的两个页签：本次执行结果 / 该能力的历史执行记录。 */
const outputTab = ref<'current' | 'history'>('current')
const HISTORY_PAGE_SIZE = 10

const history = ref<AiRecord[]>([])
const historyTotal = ref(0)
const historyPage = ref(1)
const historyLoading = ref(false)
const historyDetail = ref<AiRecord | null>(null)

/** 本次还没产出时，引导用户去看已有的历史记录。 */
const emptyDescription = computed(() =>
  historyTotal.value > 0
    ? `尚无产出，可切换到「历史产出」查看 ${historyTotal.value} 条记录`
    : '尚无产出',
)

const historyIsJson = computed(() => {
  const text = historyDetail.value?.outputText ?? ''
  if (!text.trim()) {
    return false
  }
  try {
    JSON.parse(text)
    return true
  } catch {
    return false
  }
})

const prettyHistoryJson = computed(() => {
  try {
    return JSON.stringify(JSON.parse(historyDetail.value?.outputText ?? ''), null, 2)
  } catch {
    return historyDetail.value?.outputText ?? ''
  }
})

/** 只按能力过滤，不带项目条件：同一能力在不同项目下的产出都值得回看。 */
async function loadHistory(page = 1): Promise<void> {
  historyLoading.value = true
  try {
    const result = await recordApi.list({
      capability: capability.value,
      pageNum: page,
      pageSize: HISTORY_PAGE_SIZE,
    })
    history.value = result.list
    historyTotal.value = result.total
    historyPage.value = page
    if (historyDetail.value && !result.list.some((item) => item.id === historyDetail.value?.id)) {
      historyDetail.value = null
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载历史产出失败')
  } finally {
    historyLoading.value = false
  }
}

async function openHistory(item: AiRecord): Promise<void> {
  try {
    historyDetail.value = await recordApi.get(Number(item.id))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载历史产出详情失败')
  }
}

function copyHistory(): void {
  const text = historyDetail.value?.outputText ?? ''
  if (!text) {
    ElMessage.warning('这条记录没有保存产出原文')
    return
  }
  void navigator.clipboard.writeText(text).then(
    () => ElMessage.success('已复制到剪贴板'),
    () => ElMessage.error('复制失败，请手动选择文本'),
  )
}

function downloadHistory(): void {
  const text = historyDetail.value?.outputText ?? ''
  if (!text) {
    ElMessage.warning('这条记录没有保存产出原文')
    return
  }
  const stamp = (historyDetail.value?.createdAt ?? '').replace(/[:.]/g, '-').slice(0, 19)
  downloadText(text, `${capability.value}-${stamp || 'history'}.md`)
}

/** 执行记录里的时间统一是 UTC ISO，这里转成本地时间给用户看。 */
function formatRecordTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString('zh-CN', { hour12: false })
}

function reviewText(row: AiRecord): string {
  if (row.reviewStatus === 'approved') return '已采纳'
  if (row.reviewStatus === 'rejected') return '已驳回'
  return row.reviewRequired ? '待复核' : '无需复核'
}

function reviewTagType(row: AiRecord): 'success' | 'danger' | 'warning' | 'info' {
  if (row.reviewStatus === 'approved') return 'success'
  if (row.reviewStatus === 'rejected') return 'danger'
  return row.reviewRequired ? 'warning' : 'info'
}

watch(outputTab, (tab) => {
  // 每次切到历史页签都重新拉一次：别的会话/顺序执行也会往同一能力写记录
  if (tab === 'history') {
    void loadHistory(historyPage.value)
  }
})

const isJson = computed(() => spec.value?.outputFormat === 'json')

/**
 * 可选变量以提示词模板的 optional 声明为准（后端会填「（未提供）」占位），
 * 所以表单不再强制填写，避免提示词里明明写了可选还卡住用户。
 */
function isOptionalVariable(name: string): boolean {
  return (spec.value?.optionalVariables ?? []).includes(name)
}

const prettyJson = computed(() => {
  try {
    return JSON.stringify(JSON.parse(output.value), null, 2)
  } catch {
    return output.value
  }
})

/** 脑图数据：测试点能力取模型 JSON，xmind 能力取解析出来的树。 */
const mindTree = computed<MindNode[]>(() => {
  if (capability.value === 'xmind_to_case') {
    return xmindTree.value
  }
  if (!spec.value?.mindmap || !output.value) {
    return []
  }
  try {
    return testpointToMindNodes(JSON.parse(output.value))
  } catch {
    return []
  }
})
const mindNodes = computed<MindNode[]>(() => mindTree.value)

/** 分类清单来自 store：内置中文分类 + 自定义分类。 */
const categoryOptions = computed(() => categoryStore.options)

/** 可导入的文档格式。 */
const DOC_ACCEPT = '.docx,.doc,.xlsx,.xlsm,.csv,.md,.markdown,.txt,.log,.json,.html,.htm,.pdf'

/** 可贴入的图片格式。 */
const IMAGE_ACCEPT =
  '.jpg,.jpeg,.png,.apng,.gif,.svg,.webp,.bmp,.tif,.tiff,.raw,.cr2,.nef,.arw,.dng,.psd,.ai,.heif,.heic,.avif,.jxl,.pdf'

const importing = ref(false)
const uploadingImage = ref(false)
/** 已导入的文档（文本已并入输入框，这里只留个名单）。 */
const documents = ref<DocumentAttachment[]>([])
/** 已去水印的图片，随执行请求一起发给模型。 */
const images = ref<ImageAttachment[]>([])

async function uploadDocumentFile(options: UploadRequestOptions): Promise<void> {
  importing.value = true
  try {
    const form = new FormData()
    form.append('file', options.file)
    const result = await labApi.uploadDocument(form)
    documents.value = [...documents.value, result]
    const target = primaryField.value
    if (target) {
      const current = values[target] ?? ''
      values[target] = current ? `${current}\n\n${result.text}` : result.text
    }
    if (result.truncated) {
      ElMessage.warning(`文档过长，已截取前 ${result.chars} 字`)
    } else {
      ElMessage.success(`已导入《${result.fileName}》${result.chars} 字`)
    }
    if (result.note) {
      ElMessage.info(result.note)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '文档导入失败')
  } finally {
    importing.value = false
  }
}

async function uploadImageFile(options: UploadRequestOptions): Promise<void> {
  uploadingImage.value = true
  try {
    const form = new FormData()
    form.append('file', options.file)
    const result = await labApi.uploadImage(form)
    images.value = [...images.value, result]
    ElMessage.success(result.watermarkApplied ? '图片已去水印' : result.note || '图片已处理')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '图片处理失败')
  } finally {
    uploadingImage.value = false
  }
}

/** 粘贴图片：从剪贴板里取第一张图片，先去水印再贴入。 */
async function onPaste(event: ClipboardEvent): Promise<void> {
  const items = event.clipboardData?.items
  if (!items) {
    return
  }
  for (const item of Array.from(items)) {
    if (item.kind !== 'file' || !item.type.startsWith('image/')) {
      continue
    }
    const file = item.getAsFile()
    if (!file) {
      continue
    }
    event.preventDefault()
    uploadingImage.value = true
    try {
      const dataUrl = await readAsDataUrl(file)
      const result = await labApi.uploadImageDataUrl(dataUrl)
      images.value = [...images.value, result]
      ElMessage.success(result.watermarkApplied ? '图片已去水印' : result.note || '图片已处理')
    } catch (err) {
      ElMessage.error(err instanceof Error ? err.message : '粘贴图片失败')
    } finally {
      uploadingImage.value = false
    }
    return
  }
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(new Error('读取剪贴板图片失败'))
    reader.readAsDataURL(file)
  })
}

async function removeImage(attachmentId: string): Promise<void> {
  images.value = images.value.filter((item) => item.attachmentId !== attachmentId)
  try {
    await labApi.deleteAttachment(attachmentId)
  } catch {
    // 附件删不掉不影响本次执行，忽略即可
  }
}

function resetValues(): void {
  Object.keys(values).forEach((key) => delete values[key])
  Object.keys(multiValues).forEach((key) => delete multiValues[key])
  const allFields = CAPABILITY_FORMS[capability.value] ?? []
  allFields.forEach((field) => {
    values[field.name] = field.defaultValue ?? ''
    if (field.type === 'multiselect') {
      multiValues[field.name] = []
    }
  })
  // 目标类型与框架联动：默认框架若不适用于当前目标类型，落到第一个可用项
  syncFrameworkOptions()
  const preset = String(route.query.text ?? '')
  if (preset && primaryField.value) {
    values[primaryField.value] = preset
  }
  const moduleQuery = Number(route.query.moduleId ?? 0)
  moduleId.value = moduleQuery > 0 ? moduleQuery : null
  if (moduleId.value) {
    labStore.currentModuleId = moduleId.value
  }
  syncModulePath()
}

/** 框架候选项随目标类型联动，当前选中的框架不再可选时自动换成第一个可用项。 */
function syncFrameworkOptions(): void {
  const frameworkField = (CAPABILITY_FORMS[capability.value] ?? []).find(
    (field) => field.name === 'framework',
  )
  if (!frameworkField) {
    return
  }
  const allowed = visibleOptions(frameworkField, values).map((item) => item.value)
  if (!allowed.length) {
    return
  }
  const current = values.framework ?? ''
  if (!allowed.includes(current)) {
    values.framework = allowed[0]
  }
}

/**
 * 选了功能点后，把功能点路径带进「可能归属的模块」这类字段，
 * 免得用户再手抄一遍路径。
 */
function syncModulePath(): void {
  if (!fields.value.some((field) => field.name === 'modulePath')) {
    return
  }
  const path = moduleOptions.value.find((item) => item.id === moduleId.value)?.path ?? ''
  if (path) {
    values.modulePath = path
  }
}

async function uploadXmind(options: UploadRequestOptions): Promise<void> {
  const form = new FormData()
  form.append('file', options.file)
  parsing.value = true
  try {
    const result = await labApi.parseXmind(form)
    xmindTree.value = result.tree
    values.xmindOutline = result.outlineText
    ElMessage.success('已解析 .xmind 大纲')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '解析失败')
  } finally {
    parsing.value = false
  }
}

async function parseOutlineText(): Promise<void> {
  const text = values.xmindOutline ?? ''
  if (!text.trim()) {
    ElMessage.warning('请先粘贴大纲文本')
    return
  }
  parsing.value = true
  try {
    const result = await labApi.parseOutline(text)
    xmindTree.value = result.tree
    ElMessage.success('已解析大纲')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '解析失败')
  } finally {
    parsing.value = false
  }
}

/** 脑图节点右键：按该节点单独生成用例。 */
function onNodeContext(payload: { title: string; path: string[] }): void {
  if (capability.value !== 'xmind_to_case' && capability.value !== 'testpoint_generation') {
    return
  }
  const node = findNodeByPath(mindTree.value, payload.path)
  const text = node ? subtreeText(node) : payload.path.join('\n')
  void router.push({
    path: '/lab/case_generation',
    query: { text },
  })
}

function onDegrade(info: DegradeInfo): void {
  ElMessage({ type: 'warning', message: info.message, duration: 3000, showClose: false })
}

async function run(): Promise<void> {
  const missing = fields.value
    .filter((field) => {
      if (field.type === 'multiselect') {
        return !(multiValues[field.name]?.length ?? 0) && !isOptionalVariable(field.name)
      }
      return !values[field.name]?.trim() && !isOptionalVariable(field.name)
    })
    .map((field) => field.label)
  if (missing.length) {
    ElMessage.warning(`请填写：${missing.join('、')}`)
    return
  }
  // 云端模型会把输入发到第三方，执行前先让人确认数据已脱敏
  if (!(await aiGuard.ensureExternalConfirmed())) {
    return
  }
  running.value = true
  output.value = ''
  excelArtifact.value = null
  finished.value = false
  reasoning.value = ''
  model.value = ''
  latencyMs.value = 0
  refs.value = []
  reviewRequired.value = false
  const startedAt = Date.now()
  try {
    await streamPost(
      `/lab/${capability.value}/stream`,
      {
        variables: buildVariables(),
        moduleId: moduleId.value ?? undefined,
        attachmentIds: images.value.map((item) => item.attachmentId),
        templateId: useTemplate.value && templateId.value ? templateId.value : undefined,
      },
      {
        onStart: (frame) => {
          egressWarning.value = String(frame.egressWarning ?? '')
          const list = frame.refs
          if (Array.isArray(list)) {
            refs.value = list as KnowledgeRef[]
          }
        },
        onDelta: (content) => {
          output.value += content
        },
        onReasoning: (content) => {
          reasoning.value += content
        },
        onUsage: (usage) => {
          const name = usage.model ?? usage.modelName
          if (typeof name === 'string' && name) {
            model.value = name
          }
        },
        onDegrade,
        onDone: (text, frame) => {
          if (text) {
            output.value = text
          }
          finished.value = true
          reviewRequired.value = frame.reviewRequired === true
          latencyMs.value = Date.now() - startedAt
          // 用例类能力同步生成 Excel 产出，产出面板里直接可下载
          void prepareExcelArtifact()
          // 本次执行已经入库，刷新历史列表，让计数和最新一条马上可见
          void loadHistory(1)
        },
        onError: (message) => {
          ElMessage.error(message)
        },
      },
    )
  } finally {
    running.value = false
    latencyMs.value = latencyMs.value || Date.now() - startedAt
  }
}

/**
 * 拼装提交给后端的变量：下拉与多选统一转成可读文本，
 * 多选用「、」连接，避免模型把数组序号当成步骤。
 */
function buildVariables(): Record<string, string> {
  const payload: Record<string, string> = {}
  fields.value.forEach((field) => {
    if (field.type === 'multiselect') {
      const picked = multiValues[field.name] ?? []
      payload[field.name] = picked.map((item) => optionPromptText(field, item)).join('、')
      return
    }
    const raw = values[field.name] ?? ''
    payload[field.name] = field.type === 'select' && raw ? optionPromptText(field, raw) : raw
  })
  return payload
}

function copyOutput(): void {
  if (!output.value) {
    ElMessage.warning('暂无产出')
    return
  }
  void navigator.clipboard.writeText(output.value).then(
    () => ElMessage.success('已复制到剪贴板'),
    () => ElMessage.error('复制失败，请手动选择文本'),
  )
}

function download(): void {
  if (!output.value) {
    ElMessage.warning('暂无产出')
    return
  }
  downloadText(output.value, `${capability.value}-${new Date().toISOString().slice(0, 10)}.md`)
}

function downloadText(text: string, filename: string): void {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

const knowledgeVisible = ref(false)
const savingKnowledge = ref(false)
const knowledgeForm = reactive({ title: '', category: DEFAULT_KNOWLEDGE_CATEGORY, tags: '' })

function saveToKnowledge(): void {
  knowledgeForm.title = `${spec.value?.label ?? capability.value} ${new Date().toLocaleString('zh-CN')}`
  knowledgeForm.category = DEFAULT_KNOWLEDGE_CATEGORY
  knowledgeForm.tags = ''
  knowledgeVisible.value = true
}

async function confirmSaveKnowledge(): Promise<void> {
  if (!knowledgeForm.title.trim()) {
    ElMessage.warning('标题为必填项')
    return
  }
  savingKnowledge.value = true
  try {
    await knowledgeApi.createDraft({
      title: knowledgeForm.title,
      content: output.value,
      summary: output.value.slice(0, 200),
      category: knowledgeForm.category,
      tags: knowledgeForm.tags,
      moduleId: moduleId.value,
      sourceType: 'ai_output',
      producer: capability.value,
    })
    ElMessage.success('已进入待审核池，审核通过后才计入数字资产')
    knowledgeVisible.value = false
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    savingKnowledge.value = false
  }
}

const adoptVisible = ref(false)
const adopting = ref(false)
const adoptRows = ref<AdoptCaseItem[]>([])
const requirementOptions = ref<Requirement[]>([])
const adoptForm = reactive({
  projectId: null as number | null,
  requirementId: null as number | null,
  moduleId: null as number | null,
})

/** 把不同能力的 JSON 产出归一成用例草稿结构。 */
function toAdoptItems(raw: unknown): AdoptCaseItem[] {
  if (!Array.isArray(raw)) {
    return []
  }
  const items: AdoptCaseItem[] = []
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') {
      continue
    }
    const record = entry as Record<string, unknown>
    const title = String(record.title ?? '').trim()
    if (!title) {
      continue
    }
    const rawSteps = record.steps
    const steps = Array.isArray(rawSteps)
      ? rawSteps
          .map((step) => {
            if (typeof step === 'string') {
              return { action: step, expected: '' }
            }
            const item = (step ?? {}) as Record<string, unknown>
            return {
              action: String(item.action ?? item.step ?? ''),
              expected: String(item.expected ?? ''),
            }
          })
          .filter((step) => step.action.trim() || step.expected.trim())
      : []
    items.push({
      title,
      caseType: String(record.caseType ?? 'functional'),
      priority: String(record.priority ?? 'P2'),
      preconditions: String(record.preconditions ?? ''),
      steps,
      expectedResult: String(record.expectedResult ?? record.verifyTarget ?? ''),
      tags: String(record.tags ?? record.module ?? ''),
    })
  }
  return items
}

async function openAdopt(): Promise<void> {
  try {
    const parsed = JSON.parse(output.value) as unknown
    adoptRows.value = toAdoptItems(parsed)
  } catch {
    adoptRows.value = []
    ElMessage.warning('产出不是合法 JSON，无法自动采纳')
  }
  if (!adoptRows.value.length) {
    adoptVisible.value = true
    return
  }
  adoptForm.projectId = appStore.currentProjectId
  adoptForm.moduleId = moduleId.value
  adoptForm.requirementId = null
  requirementOptions.value = []
  const projectId = adoptForm.projectId
  if (projectId) {
    try {
      requirementOptions.value = await requirementApi
        .list({ projectId, pageSize: 100 })
        .then((page) => page.list)
    } catch {
      requirementOptions.value = []
    }
  }
  adoptVisible.value = true
}

async function confirmAdopt(): Promise<void> {
  if (!adoptForm.projectId) {
    ElMessage.warning('请先选择项目')
    return
  }
  adopting.value = true
  try {
    const result = await caseApi.adopt({
      projectId: adoptForm.projectId,
      moduleId: adoptForm.moduleId,
      requirementId: adoptForm.requirementId,
      cases: adoptRows.value,
    })
    ElMessage.success(`已采纳 ${result.count} 条用例`)
    adoptVisible.value = false
    await badgeStore.load()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '采纳失败')
  } finally {
    adopting.value = false
  }
}

const reportAdoptVisible = ref(false)
const reportAdopting = ref(false)
const reportPeriod = ref<[string, string] | null>(null)
const reportTypeOptions = [
  { label: '测试总结', value: 'summary' },
  { label: '迭代报告', value: 'iteration' },
  { label: '回归报告', value: 'regression' },
  { label: '验收报告', value: 'acceptance' },
]
const reportForm = reactive({
  projectId: null as number | null,
  title: '',
  reportType: 'iteration',
})

function openAdoptReport(): void {
  reportForm.projectId = appStore.currentProjectId
  reportForm.title = `${spec.value?.label ?? '测试报告'} ${new Date().toLocaleDateString('zh-CN')}`
  reportForm.reportType = 'iteration'
  reportPeriod.value = null
  reportAdoptVisible.value = true
}

async function confirmAdoptReport(): Promise<void> {
  if (!reportForm.projectId) {
    ElMessage.warning('请先选择项目')
    return
  }
  if (!reportForm.title.trim()) {
    ElMessage.warning('标题为必填项')
    return
  }
  reportAdopting.value = true
  try {
    const report = await reportApi.adopt({
      projectId: reportForm.projectId,
      title: reportForm.title,
      reportType: reportForm.reportType,
      periodStart: reportPeriod.value?.[0] ?? '',
      periodEnd: reportPeriod.value?.[1] ?? '',
      content: output.value,
    })
    ElMessage.success('已采纳为报告草稿')
    reportAdoptVisible.value = false
    await badgeStore.load()
    void router.push(`/reports/${report.id}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '采纳失败')
  } finally {
    reportAdopting.value = false
  }
}

/** 脚本产出的入库类别：web / app / api 跟随「目标类型」，性能脚本单独入库。 */
type AutomationCategory = 'web' | 'app' | 'api' | 'performance'

const automationCategory = computed<AutomationCategory>(() => {
  const target = values.targetType ?? 'web'
  if (target === 'app' || target === 'api') {
    return target
  }
  return 'web'
})

const automationCategoryLabel = computed(() => {
  const labels: Record<AutomationCategory, string> = {
    web: 'Web',
    app: 'App',
    api: '接口',
    performance: '性能',
  }
  return labels[automationCategory.value]
})

const importingScript = ref(false)

/** 脚本名：优先用场景首行，其次用项目名，避免生成一串无意义的名字。 */
function scriptNameOf(category: AutomationCategory): string {
  const scenario = (values.scenario ?? '').split('\n')[0].trim()
  const project = (values.projectName ?? '').trim()
  const fallback = category === 'performance' ? '性能脚本' : '自动化脚本'
  return (scenario || project || fallback).slice(0, 60)
}

/**
 * 一键入库：把产出的代码块存成脚本资产，并跳到对应管理页。
 * 后端会按类别从产出里挑代码块（性能取 xml、其余取 python），前端只管传原文。
 */
async function importToAutomation(category: AutomationCategory): Promise<void> {
  if (!output.value.trim()) {
    ElMessage.warning('暂无产出')
    return
  }
  importingScript.value = true
  try {
    const script = await automationApi.createFromOutput({
      output: output.value,
      category,
      name: scriptNameOf(category),
      projectId: appStore.currentProjectId ?? undefined,
      moduleId: moduleId.value ?? undefined,
      description: values.scenario ?? '',
      framework: category === 'performance' ? 'jmeter' : values.framework ?? '',
      language: category === 'performance' ? 'xml' : 'python',
      appPlatforms:
        category === 'app' ? (multiValues.appPlatforms ?? []).join(',') : '',
      targetType: category === 'performance' ? 'performance' : category,
      source: 'ai',
    })
    ElMessage.success(`已入库：${script.scriptNo}`)
    await badgeStore.load()
    await router.push(`/automation/${category}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '入库失败')
  } finally {
    importingScript.value = false
  }
}

watch(
  () => route.query.text,
  () => {
    if (route.query.text) {
      resetValues()
    }
  },
)
watch(capability, () => {
  resetValues()
  output.value = ''
  excelArtifact.value = null
  reasoning.value = ''
  finished.value = false
  xmindTree.value = []
  // 换能力就换一套模板：类别不同，已选模板不能再沿用
  useTemplate.value = false
  templateId.value = null
  templateOptions.value = []
  // 换能力就换一批历史：列表、计数、当前查看的记录都要跟着清掉
  outputTab.value = 'current'
  history.value = []
  historyTotal.value = 0
  historyPage.value = 1
  historyDetail.value = null
  void loadHistory(1)
})

onMounted(async () => {
  await labStore.loadAllCapabilities()
  await labStore.loadModuleOptions(appStore.currentProjectId ?? undefined)
  resetValues()
  void loadHistory(1)
  void categoryStore.load()
  window.addEventListener('paste', onPaste)
})

onUnmounted(() => {
  window.removeEventListener('paste', onPaste)
})

watch(
  () => appStore.currentProjectId,
  (projectId) => {
    void labStore.loadModuleOptions(projectId ?? undefined)
  },
)

watch(moduleId, () => {
  syncModulePath()
})

// 目标类型变化时，框架候选项随之变化，已选框架不再适用就自动切换
watch(
  () => values.targetType,
  () => {
    syncFrameworkOptions()
  },
)
</script>

<style scoped>
.cap-tag {
  margin-left: 8px;
  vertical-align: middle;
}

/* 标题下方的提示词来源与说明 */
.cap-sub {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: -4px 0 12px;
  font-size: 13px;
  color: var(--aitp-text-weak);
}

.cap-sub__prompt {
  font-family: 'Cascadia Mono', Consolas, monospace;
  padding: 1px 6px;
  border: 1px solid var(--aitp-border);
  border-radius: 4px;
  background: var(--aitp-subtle);
}

.cap-field-label {
  /* 标签列窄，"选填"标记放不下时整体换到下一行，别把标签文字拆散 */
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 2px 6px;
  width: 100%;
}

.cap-field-label__text {
  white-space: nowrap;
}

.cap-field-optional {
  font-size: 11px;
  height: 18px;
  padding: 0 4px;
}

.cap-field-select {
  width: 100%;
}

/* 产出物模板：开关 + 同类模板下拉 */
.cap-template {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  width: 100%;
}

.cap-template__select {
  width: 240px;
}

.cap-template__kind {
  margin-left: 8px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.cap-template__hint {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

/* 附件区：文档导入 + 图片贴入 */
.cap-attachments {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.cap-attachments__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.cap-attachments__hint {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.cap-attachments__docs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.cap-attachments__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cap-attachment {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px;
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
}

.cap-attachment__thumb {
  width: 56px;
  height: 40px;
  object-fit: cover;
  border-radius: 4px;
  background: var(--aitp-bg);
}

.cap-attachment__meta {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  flex: 1;
  min-width: 0;
}

.cap-attachment__name {
  font-size: 12px;
  color: var(--aitp-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.cap-layout {
  display: grid;
  grid-template-columns: minmax(340px, 460px) 1fr;
  gap: 12px;
  align-items: start;
}

.cap-input {
  max-height: calc(100vh - 150px);
  overflow-y: auto;
}

.cap-output {
  min-height: 420px;
}

.cap-output__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin-bottom: 10px;
}

.cap-output__loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--aitp-text-weak);
  margin-bottom: 10px;
}

/* 本次执行生成的 Excel 产出：一条常驻的下载条 */
.cap-artifact {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  margin-bottom: 10px;
  border: 1px solid var(--el-color-success-light-5, #b3e19d);
  border-radius: 6px;
  background: var(--aitp-subtle);
}

.cap-artifact__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.cap-artifact__title {
  font-size: 13px;
  font-weight: 600;
}

.cap-artifact__meta {
  font-size: 12px;
  color: var(--aitp-text-weak);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---- 历史产出：左列表 + 右原文 ---- */
.cap-history {
  display: grid;
  grid-template-columns: minmax(200px, 240px) 1fr;
  gap: 12px;
  align-items: start;
}

.cap-history__side {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  padding-right: 12px;
  border-right: 1px solid var(--aitp-border);
}

.cap-history__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: calc(100vh - 340px);
  min-height: 280px;
  overflow-y: auto;
}

.cap-history__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: 8px 10px;
  text-align: left;
  font: inherit;
  color: var(--aitp-text);
  background: var(--aitp-panel);
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}

.cap-history__item:hover {
  border-color: var(--el-color-primary);
  background: var(--aitp-hover);
}

.cap-history__item.is-active {
  border-color: var(--el-color-primary);
  background: var(--aitp-primary-soft);
}

.cap-history__item-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.cap-history__time {
  font-size: 12px;
  color: var(--aitp-text-weak);
  white-space: nowrap;
}

.cap-history__summary {
  font-size: 13px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.cap-history__meta {
  display: flex;
  gap: 10px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.cap-history__pager {
  justify-content: center;
}

.cap-history__view {
  min-width: 0;
  max-height: calc(100vh - 340px);
  overflow-y: auto;
}

.cap-history__view-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 6px 12px;
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--aitp-border);
}

.cap-history__view-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  min-width: 0;
}

.cap-history__view-actions {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.cap-history__view-actions :deep(.el-button + .el-button) {
  margin-left: 8px;
}

.cap-output__json {
  background: var(--aitp-subtle);
  border: 1px solid var(--aitp-border);
  border-radius: 6px;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  margin: 0;
}

.mindmap-hint {
  margin-top: 10px;
}

.cap-refs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--aitp-border);
}

.cap-refs__title {
  font-size: 13px;
  color: var(--aitp-text-weak);
}

.cap-refs__item {
  cursor: pointer;
}

.egress-alert {
  margin-top: 4px;
}

/*
 * 操作按钮会换行（面板窄的时候），用 gap 统一控制横竖间距。
 * Element Plus 默认给相邻按钮加 margin-left:12px，换行后第一颗按钮会多出一个左边距、
 * 两行之间也会顶在一起，这里把它清掉改由 gap 负责。
 */
.cap-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 12px;
  width: 100%;
}

.cap-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.cap-actions__hint {
  font-size: 12px;
  line-height: 1.5;
  color: var(--aitp-text-weak);
}

/* 产出物操作条：复制 / 下载 / 导出 / 采纳 / 入库都挂在产出区 */
.cap-output__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.cap-output__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.adopt-alert {
  margin-top: 12px;
}
</style>
