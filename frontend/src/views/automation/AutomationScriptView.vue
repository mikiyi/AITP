<template>
  <div class="page">
    <div class="page-header">
      <div class="auto-head">
        <h2 class="page-title">{{ categoryInfo?.label || '自动化脚本' }}</h2>
        <span class="muted">{{ categoryHint }}</span>
      </div>
      <div class="auto-head__actions">
        <el-button :icon="Refresh" @click="reloadAll">刷新</el-button>
        <!-- 元素库（Page Object）只对 Web / App 脚本有意义：存的是浏览器 / APP 元素选择器，
             接口与性能脚本用不到，不该在这两个入口出现 -->
        <el-button v-if="category === 'web' || category === 'app'" @click="openElements">元素库</el-button>
        <el-button @click="openSchedules">定时</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建脚本</el-button>
      </div>
    </div>

    <el-alert
      v-if="runtimeHint.length"
      :type="runtimeHintType"
      :closable="false"
      show-icon
      class="auto-alert"
    >
      <template #title>{{ runtimeHint[0] }}</template>
      <div v-for="line in runtimeHint.slice(1)" :key="line">{{ line }}</div>
    </el-alert>

    <div class="auto-stats">
      <MetricCard label="脚本总数" :value="stats.scriptCount" />
      <MetricCard label="执行次数" :value="stats.runTotal" />
      <MetricCard label="执行成功" :value="stats.runSuccess" tone="success" />
      <MetricCard label="执行失败" :value="stats.runFailed" tone="danger" />
    </div>

    <div class="panel panel-pad auto-block">
      <div class="auto-block__head">
        <h3>脚本列表</h3>
        <span class="muted">{{ runnerText }}</span>
      </div>
      <BaseProTable
        ref="tableRef"
        :columns="columns"
        :fetcher="fetcher"
        :search-fields="searchFields"
        :max-height="440"
        selectable
        sync-query
        query-prefix="auto_"
      >
        <template #batch="{ rows }">
          <el-button type="primary" size="small" :loading="batchBusy" @click="batchExecute(rows)">批量后台执行</el-button>
        </template>
        <template #name="{ row }">
          <div class="auto-name">
            <span class="auto-name__title">{{ row.name }}</span>
            <span v-if="row.envId" class="muted">{{ envNameOf(row.envId) }}</span>
          </div>
        </template>
        <template #framework="{ row }">
          <span v-if="row.framework">{{ frameworkLabel(row.framework) }}</span>
          <span v-else class="muted">-</span>
        </template>
        <template #lastRunStatus="{ row }">
          <el-tag v-if="row.lastRunStatus" :type="runTagType(row.lastRunStatus)" size="small" effect="plain">
            {{ runStatusText(row.lastRunStatus) }}
          </el-tag>
          <span v-else class="muted">未执行</span>
        </template>
        <template #lastRunAt="{ row }">
          <span :class="{ muted: !row.lastRunAt }">{{ dateTimeText(row.lastRunAt) }}</span>
        </template>
        <template #referenced="{ row }">
          <ReferenceRefs :refs="row.refs" :used="row.referenced" />
        </template>
        <template #status="{ row }">
          <el-tag :type="row.status === 'ready' ? 'success' : 'info'" size="small" effect="plain">
            {{ row.status === 'ready' ? '启用' : '停用' }}
          </el-tag>
        </template>
        <template #actions="{ row }">
          <el-button link type="primary" @click.stop="openEdit(row)">编辑</el-button>
          <el-button link type="warning" @click.stop="copyScript(row)">复制</el-button>
          <el-button link type="success" :loading="executingId === row.id" :disabled="executingId !== null && executingId !== row.id" @click.stop="execute(row)">执行</el-button>
          <el-button link type="primary" :disabled="executingId !== null" @click.stop="executeAsync(row)">后台</el-button>
          <el-button link type="success" @click.stop="openKnowledge(row)">加入知识库</el-button>
          <el-button link type="info" @click.stop="openHistory(row)">历史</el-button>
          <el-button link type="warning" @click.stop="openVersions(row)">版本</el-button>
          <el-button link type="danger" @click.stop="removeScript(row)">删除</el-button>
        </template>
      </BaseProTable>
    </div>

    <!-- 脚本编辑 -->
    <el-dialog
      v-model="editorVisible"
      :title="editingId ? '编辑脚本' : '新建脚本'"
      width="900px"
      top="6vh"
    >
      <el-form label-width="110px">
        <el-form-item label="脚本名称" required>
          <el-input v-model="form.name" placeholder="例如：登录主流程冒烟" maxlength="200" />
        </el-form-item>
        <el-form-item label="框架">
          <el-select v-model="form.framework" clearable placeholder="选择自动化框架" style="width: 100%">
            <el-option v-for="item in frameworkOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <div v-if="selectedFramework && !selectedFramework.ready" class="framework-hint">
            <p>
              当前解释器
              <span class="text-mono">{{ runtime?.pythonInterpreter || '未指定' }}</span>
              未就绪，缺：{{ (selectedFramework.missing || []).join('、') || '依赖' }}。
            </p>
            <p>{{ selectedFramework.setupHint }}</p>
            <p v-if="selectedFramework.installCommand" class="framework-hint__cmd text-mono">
              {{ selectedFramework.installCommand }}
            </p>
            <div v-if="alternativeInterpreters.length" class="framework-hint__alt">
              <span>本机已有解释器装好该框架：</span>
              <el-button
                v-for="item in alternativeInterpreters"
                :key="item.path"
                link
                type="primary"
                @click="useInterpreter(item.path)"
              >
                {{ item.path }}（Python {{ item.version }}）
              </el-button>
            </div>
            <el-button link type="primary" @click="router.push('/settings/automation')">
              打开解释器体检
            </el-button>
          </div>
        </el-form-item>
        <el-form-item v-if="category !== 'performance'" label="运行方式">
          <el-radio-group v-model="form.runMode">
            <el-radio value="script">直接执行</el-radio>
            <el-radio value="pytest">pytest</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="附加参数">
          <el-input
            v-model="form.entryCommand"
            placeholder="例如 -k smoke（作为参数直接传给解释器，不走 shell）"
          />
        </el-form-item>
        <el-form-item label="超时秒数">
          <el-input-number v-model="form.timeoutSeconds" :min="1" :max="7200" style="width: 200px" />
        </el-form-item>
        <el-form-item label="默认环境">
          <el-select v-model="form.envId" placeholder="不绑定环境" clearable style="width: 100%">
            <el-option
              v-for="item in envs"
              :key="item.id"
              :label="`${item.name}${item.kindLabel ? `（${item.kindLabel}）` : ''}`"
              :value="item.id"
            />
          </el-select>
          <p class="env-hint">执行留痕会记录这套环境，生产环境执行前会再次确认。</p>
        </el-form-item>
        <el-form-item label="覆盖用例方式">
          <el-radio-group v-model="form.caseMatchMode">
            <el-radio value="case_no">按用例编号</el-radio>
            <el-radio value="tag">按用例标签</el-radio>
          </el-radio-group>
          <p class="env-hint">
            {{
              form.caseMatchMode === 'case_no'
                ? '脚本执行结果按用例编号匹配到测试轮次里的用例。'
                : '脚本执行结果按标签匹配到测试轮次里的用例。'
            }}
          </p>
        </el-form-item>
        <el-form-item label="覆盖用例值">
          <el-input
            v-model="form.caseMatchValue"
            :placeholder="form.caseMatchMode === 'case_no' ? '例如 FT-001,FT-002' : '例如 smoke'"
            maxlength="500"
          />
          <p class="env-hint">
            多个值用英文逗号分隔；留空表示脚本不绑定用例，执行结果只留在自动化历史里。
          </p>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>
        <el-form-item v-if="editingId" label="变更说明">
          <el-input
            v-model="form.changeNote"
            placeholder="本次正文改了什么，评审版本时对照用（可不填）"
            maxlength="200"
          />
        </el-form-item>
        <el-form-item :label="category === 'performance' ? 'JMeter 脚本' : '脚本正文'">
          <el-input
            v-model="form.content"
            type="textarea"
            :rows="16"
            class="text-mono"
            :placeholder="codePlaceholder"
          />
        </el-form-item>
        <el-form-item label="数据库脚本">
          <div class="auto-db-refs">
            <div class="auto-db-refs__row">
              <span class="auto-db-refs__label">执行前预处理</span>
              <el-select
                v-model="form.preDbRefs"
                multiple
                filterable
                clearable
                placeholder="选择 SQL 脚本（造数/初始化）"
                style="flex: 1"
              >
                <el-option
                  v-for="s in sqlScriptOptions"
                  :key="s.scriptNo"
                  :label="`${s.scriptNo} ${s.alias ? `· ${s.alias}` : ''} ${s.name}`"
                  :value="s.scriptNo"
                />
              </el-select>
            </div>
            <div class="auto-db-refs__row">
              <span class="auto-db-refs__label">执行后处理</span>
              <el-select
                v-model="form.postDbRefs"
                multiple
                filterable
                clearable
                placeholder="选择 SQL 脚本（清理/收尾）"
                style="flex: 1"
              >
                <el-option
                  v-for="s in sqlScriptOptions"
                  :key="s.scriptNo"
                  :label="`${s.scriptNo} ${s.alias ? `· ${s.alias}` : ''} ${s.name}`"
                  :value="s.scriptNo"
                />
              </el-select>
            </div>
            <div class="auto-db-refs__row">
              <span class="auto-db-refs__label">结果断言</span>
              <el-select
                v-model="form.dbAssertRefs"
                multiple
                filterable
                clearable
                placeholder="选择 SQL 脚本（只读查询）"
                style="flex: 1"
              >
                <el-option
                  v-for="s in sqlScriptOptions"
                  :key="s.scriptNo"
                  :label="`${s.scriptNo} ${s.alias ? `· ${s.alias}` : ''} ${s.name}`"
                  :value="s.scriptNo"
                />
              </el-select>
            </div>
            <p class="muted" style="font-size: 12px; margin-top: 6px">
              按编号拉取数据库脚本：执行前预处理 / 执行后处理允许写操作（造数/清理）；
              结果断言走只读查询，断言未通过时脚本判定为失败。
            </p>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveScript">保存</el-button>
      </template>
    </el-dialog>

    <!-- 执行结果 -->
    <el-dialog v-model="resultVisible" title="执行结果" width="860px" top="6vh">
      <div v-if="lastRun" class="auto-result">
        <div class="auto-result__head">
          <el-tag :type="runTagType(lastRun.status)" effect="plain">
            {{ runStatusText(lastRun.status) }}
          </el-tag>
          <span class="muted">退出码 {{ lastRun.exitCode ?? '-' }}</span>
          <span class="muted">耗时 {{ lastRun.durationMs ?? 0 }} ms</span>
          <span class="muted text-mono">{{ lastRun.runNo }}</span>
        </div>
        <div class="auto-result__cmd text-mono">{{ lastRun.commandLine || '（未启动进程）' }}</div>
        <el-alert
          v-if="lastRun.errorMessage"
          type="error"
          :closable="false"
          show-icon
          :title="lastRun.errorMessage"
          class="auto-alert"
        />
        <div v-if="lastRun.writebackStatus" class="auto-writeback">
          <el-tag :type="writebackTagType(lastRun.writebackStatus)" size="small" effect="plain">
            {{ lastRun.writebackLabel || lastRun.writebackStatus }}
          </el-tag>
          <span class="auto-writeback__summary">{{ lastRun.writebackSummary }}</span>
        </div>
        <el-table
          v-if="lastRun.caseResults?.length"
          :data="lastRun.caseResults"
          size="small"
          border
          max-height="240"
          class="auto-writeback__table"
        >
          <el-table-column prop="caseNo" label="用例编号" width="120" />
          <el-table-column prop="testName" label="测试名称" min-width="200" show-overflow-tooltip />
          <el-table-column label="回写结果" width="100">
            <template #default="{ row }">
              <el-tag :type="caseResultTagType(row.result)" size="small" effect="plain">
                {{ caseResultText(row.result) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="匹配方式" width="100">
            <template #default="{ row }">{{ mappedByText(row.mappedBy) }}</template>
          </el-table-column>
          <el-table-column prop="message" label="说明" min-width="180" show-overflow-tooltip />
        </el-table>
        <el-tabs v-model="resultTab">
          <el-tab-pane label="标准输出" name="stdout">
            <pre class="auto-result__out text-mono">{{ lastRun.stdout || '（无输出）' }}</pre>
          </el-tab-pane>
          <el-tab-pane label="错误输出" name="stderr">
            <pre class="auto-result__out text-mono">{{ lastRun.stderr || '（无输出）' }}</pre>
          </el-tab-pane>
          <el-tab-pane :label="`产物（${lastRun.artifacts?.length ?? 0}）`" name="artifacts">
            <div v-if="lastRun.artifacts?.length" class="auto-artifacts">
              <div v-for="item in lastRun.artifacts" :key="item.path" class="auto-artifacts__row">
                <span class="text-mono auto-artifacts__path">{{ item.path }}</span>
                <span class="muted">{{ formatSize(item.size) }}</span>
                <el-button
                  v-if="isImage(item.path)"
                  link
                  type="primary"
                  @click="openPreview(lastRun.id, item.path)"
                >预览</el-button>
                <a :href="artifactUrl(lastRun.id, item.path)" class="auto-artifacts__link">
                  <el-button link type="primary" :icon="Download">下载</el-button>
                </a>
              </div>
            </div>
            <div v-else class="muted auto-artifacts__empty">
              <p>本次执行没有产物。失败截图是排查的关键：脚本把截图存到环境变量
                <code>AITP_RUN_DIR</code> 指向的目录，就会出现在这里（图片可直接点「预览」）。</p>
              <p>Selenium 失败留截图，在 except 里加一行：</p>
              <pre class="text-mono">import os; driver.save_screenshot(os.path.join(os.environ["AITP_RUN_DIR"], "fail.png"))</pre>
              <p>Playwright 同步 API：</p>
              <pre class="text-mono">page.screenshot(path=os.path.join(os.environ["AITP_RUN_DIR"], "fail.png"))</pre>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
      <template #footer>
        <a v-if="lastRun" :href="reportUrl(lastRun.id)" class="auto-report-link">
          <el-button type="primary" plain :icon="Download">导出报告</el-button>
        </a>
        <el-button @click="resultVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 截图等图片产物直接预览（嵌在执行结果弹窗里，需 append-to-body 才不被遮挡） -->
    <el-dialog
      v-model="previewVisible"
      :title="previewArtifact?.path"
      width="720px"
      top="6vh"
      append-to-body
    >
      <div v-if="previewArtifact" class="auto-preview">
        <img :src="artifactUrl(previewArtifact.runId, previewArtifact.path)" alt="产物预览" />
      </div>
    </el-dialog>

    <!-- 执行历史 -->
    <el-dialog v-model="historyVisible" title="执行历史" width="1020px" top="6vh">
      <el-table :data="historyRows" size="small" border max-height="420">
        <el-table-column prop="runNo" label="执行编号" width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="runTagType(row.status)" size="small" effect="plain">
              {{ runStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="退出码" width="80" align="right">
          <template #default="{ row }">{{ row.exitCode ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="90" align="right">
          <template #default="{ row }">{{ row.durationMs ?? 0 }} ms</template>
        </el-table-column>
        <el-table-column label="环境" width="120">
          <template #default="{ row }">{{ envNameOf(row.envId) || '-' }}</template>
        </el-table-column>
        <el-table-column label="回写" width="130">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.writebackStatus"
              :content="row.writebackSummary || '-'"
              placement="top"
              popper-class="auto-writeback__tip"
            >
              <el-tag :type="writebackTagType(row.writebackStatus)" size="small" effect="plain">
                {{ row.writebackLabel || row.writebackStatus }}
              </el-tag>
            </el-tooltip>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="开始时间" width="170">
          <template #default="{ row }">{{ dateTimeText(row.startedAt) }}</template>
        </el-table-column>
        <el-table-column prop="errorMessage" label="说明" min-width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button link type="primary" @click="openRunDetail(row)">详情</el-button>
            <a :href="reportUrl(row.id)" class="auto-report-link">
              <el-button link type="primary">报告</el-button>
            </a>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="historyVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- P1 #7 版本差异/评审 -->
    <el-dialog v-model="versionVisible" title="脚本版本" width="900px" top="5vh">
      <el-table :data="versions" size="small" border max-height="220">
        <el-table-column prop="versionNo" label="版本" width="70" />
        <el-table-column prop="changeNote" label="变更说明" min-width="160" show-overflow-tooltip />
        <el-table-column label="评审" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.reviewStatus === 'approved' ? 'success' : row.reviewStatus === 'rejected' ? 'danger' : 'info'" effect="plain">
              {{ row.reviewStatus === 'approved' ? '通过' : row.reviewStatus === 'rejected' ? '驳回' : '待审' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button link type="primary" @click="showDiff(String(row.id))">与当前对比</el-button>
            <el-button link type="success" @click="review(row, 'approved')">通过</el-button>
            <el-button link type="danger" @click="review(row, 'rejected')">驳回</el-button>
          </template>
        </el-table-column>
      </el-table>
      <pre v-if="diffText" class="version-diff">{{ diffText }}</pre>
      <template #footer>
        <el-button @click="versionVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- P1 #8 元素库 -->
    <el-dialog v-model="elementVisible" title="元素库（Page Object）" width="860px" top="5vh">
      <div class="element-form">
        <el-input v-model="elementForm.page" placeholder="页面" style="width: 120px" />
        <el-input v-model="elementForm.name" placeholder="元素名" style="width: 140px" />
        <el-input v-model="elementForm.selector" placeholder="选择器" style="flex: 1" />
        <el-select v-model="elementForm.kind" style="width: 90px">
          <el-option v-for="k in ['css', 'xpath', 'text', 'role']" :key="k" :label="k" :value="k" />
        </el-select>
        <el-button type="primary" @click="saveElement">添加</el-button>
      </div>
      <el-table :data="elements" size="small" border max-height="320">
        <el-table-column prop="page" label="页面" width="110" />
        <el-table-column prop="name" label="元素名" width="140" />
        <el-table-column prop="selector" label="选择器" min-width="200" show-overflow-tooltip />
        <el-table-column prop="kind" label="类型" width="80" />
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeElement(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <p class="muted" style="font-size: 12px; margin-top: 8px">
        Web/App 脚本执行时自动注入 AITP_ELEMENTS_JSON，脚本可按名称取选择器，避免硬编码。
      </p>
      <template #footer>
        <el-button @click="elementVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- P2 定时调度 -->
    <el-dialog v-model="scheduleVisible" title="定时调度" width="860px" top="5vh">
      <div class="element-form">
        <el-input v-model="scheduleForm.name" placeholder="调度名" style="width: 140px" />
        <el-select v-model="scheduleForm.targetType" style="width: 100px" @change="onScheduleTypeChange">
          <el-option label="场景" value="scenario" />
          <el-option label="脚本" value="script" />
        </el-select>
        <el-select
          v-model="scheduleForm.targetId"
          filterable
          :placeholder="scheduleForm.targetType === 'scenario' ? '选择场景（编号 名称）' : '选择脚本（编号 名称）'"
          style="width: 260px"
        >
          <el-option v-for="item in scheduleTargetOptions" :key="item.id" :label="item.label" :value="item.id" />
        </el-select>
        <el-input-number v-model="scheduleForm.intervalMinutes" :min="1" style="width: 110px" />
        <span class="muted">分钟</span>
        <el-button type="primary" @click="saveSchedule">添加</el-button>
      </div>
      <el-table :data="schedules" size="small" border max-height="300">
        <el-table-column prop="scheduleNo" label="编号" width="100" />
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column label="目标" width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ scheduleTargetText(row) }}</template>
        </el-table-column>
        <el-table-column prop="intervalMinutes" label="间隔(分)" width="80" />
        <el-table-column label="启用" width="70">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v: boolean) => toggleSchedule(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="下次运行" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ dateTimeText(row.nextRunAt) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button link type="primary" @click="triggerSchedule(row)">立即</el-button>
            <el-button link type="danger" @click="removeSchedule(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="scheduleVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>

    <AddToKnowledgeDialog
      v-model="knowledgeVisible"
      :title="knowledgePayload.title"
      :content="knowledgePayload.content"
      ref-type="automation_script"
      :ref-id="knowledgePayload.refId"
      :ref-label="knowledgePayload.refLabel"
    />
    <ProdConfirmDialog ref="prodDialog" />
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Plus, Refresh } from '@element-plus/icons-vue'
import BaseProTable, { type ColumnDef, type SearchField, type TableExpose } from '@/components/BaseProTable.vue'
import MetricCard from '@/components/MetricCard.vue'
import ReferenceRefs from '@/components/ReferenceRefs.vue'
import AddToKnowledgeDialog from '@/components/AddToKnowledgeDialog.vue'
import ProdConfirmDialog from '@/components/ProdConfirmDialog.vue'
import { automationApi, apiScenarioApi, sqlScriptApi, testEnvApi, schedulesApi } from '@/api'
import { useAppStore } from '@/stores/app'
import { dateTimeText } from '@/utils/format'
import type {
  ApiScenario,
  AutomationCategory,
  AutomationCategoryInfo,
  AutomationInterpreters,
  AutomationRun,
  AutomationRuntime,
  AutomationScript,
  SqlScript,
  TestEnv,
} from '@/api/types'

const route = useRoute()
const router = useRouter()

const knowledgeVisible = ref(false)
const prodDialog = ref<InstanceType<typeof ProdConfirmDialog> | null>(null)
const knowledgePayload = reactive({
  title: '',
  content: '',
  refId: null as number | null,
  refLabel: '',
})

function openKnowledge(row: AutomationScript): void {
  knowledgePayload.title = row.name
  knowledgePayload.content = [
    `# ${row.name}`,
    row.description || '',
    `**类别**：${row.categoryLabel || row.category}`,
    `**框架**：${row.framework || '-'}　**语言**：${row.language || '-'}`,
    row.content
      ? '```' + (row.language || '') + '\n' + row.content + '\n' + '```'
      : '',
  ].filter(Boolean).join('\n\n')
  knowledgePayload.refId = Number(row.id)
  knowledgePayload.refLabel = row.name
  knowledgeVisible.value = true
}

/** 四个二级菜单共用这一个页面，类别从路由里取。 */
const category = computed<AutomationCategory>(() => {
  const raw = String(route.params.category || 'web')
  if (raw === 'app' || raw === 'api' || raw === 'performance') {
    return raw
  }
  return 'web'
})

const CATEGORY_HINTS: Record<AutomationCategory, string> = {
  web: 'Web 端自动化用例，用 Python 解释器直接执行',
  app: '移动端自动化用例，用 Python 解释器直接执行',
  api: '接口自动化用例，用 Python 解释器直接执行',
  performance: 'JMeter 性能脚本，用本地 JMeter 命令行执行',
}

const categoryHint = computed(() => CATEGORY_HINTS[category.value])

/** 框架清单由后端 automation_frameworks.py 给出，页面只按当前类别筛，不再自己维护一份。 */
const frameworkChoices = computed(() =>
  (runtime.value?.frameworks ?? []).filter((item) => item.targets.includes(category.value)),
)

/** 运行时体检未返回时的框架中文名兜底，避免下拉短暂露出代码。 */
const FRAMEWORK_LABELS: Record<string, string> = {
  selenium: 'Selenium',
  playwright: 'Playwright',
  pytest: 'Pytest',
  requests: 'Requests',
  unittest: 'Unittest',
  appium: 'Appium（iOS / Android）',
  uiautomator2: 'UiAutomator2（Android）',
  hypium: 'Hypium（鸿蒙 HOS）',
  jmeter: 'JMeter',
}

const frameworkOptions = computed(() => {
  const list = frameworkChoices.value.map((item) => ({ label: item.label, value: item.code }))
  if (list.length) {
    return list
  }
  return (FRAMEWORK_PREFERENCE[category.value] ?? []).map((code) => ({
    label: FRAMEWORK_LABELS[code] ?? code,
    value: code,
  }))
})

const selectedFramework = computed(() =>
  frameworkChoices.value.find((item) => item.code === form.framework) ?? null,
)

function frameworkLabel(value: string): string {
  if (!value) {
    return '-'
  }
  return (runtime.value?.frameworks ?? []).find((item) => item.code === value)?.label ?? value
}

/** 本机别的解释器里已经装好当前框架依赖的，可以直接一键切过去。 */
const alternativeInterpreters = computed(() => {
  const packageName = selectedFramework.value?.package
  if (!packageName) {
    return []
  }
  return (interpreters.value?.candidates ?? []).filter(
    (item) => !item.isCurrent && item.packages[packageName],
  )
})

async function useInterpreter(path: string): Promise<void> {
  try {
    runtime.value = await automationApi.selectInterpreter(path)
    ElMessage.success(`执行解释器已切换为 ${path}`)
    await reloadAll()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '切换解释器失败')
  }
}

const codePlaceholder = computed(() =>
  category.value === 'performance'
    ? '<?xml version="1.0" encoding="UTF-8"?>\n<jmeterTestPlan version="1.2">…</jmeterTestPlan>'
    : 'print("hello")',
)

const categories = ref<AutomationCategoryInfo[]>([])
const runtime = ref<AutomationRuntime | null>(null)
const interpreters = ref<AutomationInterpreters | null>(null)
const envs = ref<TestEnv[]>([])

function envNameOf(envId?: number | null): string {
  if (!envId) {
    return ''
  }
  return envs.value.find((item) => item.id === envId)?.name ?? ''
}
const categoryInfo = computed(() => categories.value.find((item) => item.category === category.value))

const stats = computed(() => {
  const info = categoryInfo.value
  const runStats = info?.runStats ?? { total: 0, success: 0, failed: 0 }
  return {
    scriptCount: info?.scriptCount ?? 0,
    runTotal: runStats.total,
    runSuccess: runStats.success,
    runFailed: runStats.failed,
  }
})

const runnerText = computed(() =>
  category.value === 'performance'
    ? '执行方式：本地 JMeter 命令行（.jmx）'
    : '执行方式：本机 Python 解释器',
)

const runtimeHint = computed<string[]>(() => {
  const info = runtime.value
  if (!info) {
    return []
  }
  if (category.value === 'performance') {
    if (info.jmeterAvailable) {
      return [`JMeter 已就绪：${info.jmeterCommand}`]
    }
    return ['未找到本地 JMeter，请到「设置 → 性能测试管理设置」填写 JMeter 安装根目录后再执行。']
  }
  const lines: string[] = []
  if (info.pythonInterpreterAvailable) {
    lines.push(
      `Python 解释器已就绪：${info.pythonInterpreter}${info.pythonVersion ? `（${info.pythonVersion}）` : ''}`,
    )
  } else {
    lines.push('Python 解释器不可用，请到「设置 → 自动化管理设置」指定解释器路径后再执行。')
  }
  if (category.value === 'app') {
    if (info.appiumServerAvailable) {
      lines.push(`Appium Server 已连通：${info.appiumServerUrl}`)
      const devices = info.androidDevices ?? []
      lines.push(
        devices.length
          ? `已连接设备：${devices.map((item) => item.serial).join('、')}`
          : '未检测到已连接的 Android 设备（adb devices 为空），启动模拟器或连接真机后再执行。',
      )
    } else {
      lines.push(
        `Appium Server 未连通：${info.appiumServerUrl || '未配置'}，请先启动 Appium 服务，到「设置 → 自动化管理设置」可改地址。`,
      )
    }
    if (info.adbAvailable === false) {
      lines.push('未找到 adb 命令，请到「设置 → 自动化管理设置」填写 adb 的完整路径。')
    }
  }
  if (category.value === 'web' && info.playwrightBrowsersAvailable === false) {
    lines.push('Playwright 浏览器内核未下载：在执行解释器里运行 python -m playwright install chromium。')
  }
  return lines
})

const runtimeHintType = computed<'success' | 'warning'>(() => {
  const info = runtime.value
  if (!info) {
    return 'warning'
  }
  if (category.value === 'performance') {
    return info.jmeterAvailable ? 'success' : 'warning'
  }
  if (!info.pythonInterpreterAvailable) {
    return 'warning'
  }
  if (category.value === 'app' && (!info.appiumServerAvailable || info.adbAvailable === false)) {
    return 'warning'
  }
  if (category.value === 'web' && info.playwrightBrowsersAvailable === false) {
    return 'warning'
  }
  return 'success'
})

const tableRef = ref<TableExpose | null>(null)

const columns: ColumnDef[] = [
  { prop: 'scriptNo', label: '编号', width: 110 },
  { prop: 'name', label: '脚本', minWidth: 220, slot: 'name' },
  { prop: 'framework', label: '框架', width: 110, slot: 'framework' },
  { prop: 'fileName', label: '文件名', width: 180 },
  { prop: 'referenced', label: '是否被引用', width: 110, align: 'center', slot: 'referenced' },
  { prop: 'status', label: '状态', width: 90, slot: 'status' },
  { prop: 'lastRunStatus', label: '最近执行', width: 110, slot: 'lastRunStatus' },
  { prop: 'lastRunAt', label: '最近执行时间', width: 170, slot: 'lastRunAt' },
  { prop: 'actions', label: '操作', width: 440, slot: 'actions' },
]

const searchFields = computed<SearchField[]>(() => [
  { prop: 'keyword', label: '脚本名称 / 编号' },
  {
    prop: 'framework',
    label: '框架',
    type: 'select',
    options: frameworkOptions.value,
  },
  {
    prop: 'referenced',
    label: '是否被引用',
    type: 'select',
    options: [
      { label: '全部', value: '' },
      { label: '已被引用', value: 'true' },
      { label: '未被引用', value: 'false' },
    ],
  },
  {
    prop: 'status',
    label: '状态',
    type: 'select',
    options: [
      { label: '启用', value: 'ready' },
      { label: '停用', value: 'disabled' },
    ],
  },
])

function fetcher(query: Record<string, unknown>) {
  const q: Record<string, unknown> = { ...query, category: category.value }
  const refVal = String(q.referenced ?? '').trim()
  if (refVal !== 'true' && refVal !== 'false') delete q.referenced
  return automationApi.list(q as never)
}

// ---- 执行 ----
const resultVisible = ref(false)
const resultTab = ref('stdout')
const lastRun = ref<AutomationRun | null>(null)

async function confirmProdEnv(row: AutomationScript): Promise<boolean> {
  const env = envs.value.find((item) => item.id === row.envId)
  if (!env || env.kind !== 'prod') {
    return true
  }
  return (await prodDialog.value?.open(`脚本绑定的是生产环境「${env.name}」，确认执行？`)) ?? false
}

const executingId = ref<number | null>(null)

async function execute(row: AutomationScript): Promise<void> {
  if (executingId.value !== null) {
    return
  }
  if (!(await confirmProdEnv(row))) {
    return
  }
  executingId.value = row.id
  try {
    const result = await automationApi.run(row.id)
    lastRun.value = await automationApi.runDetail(result.id).catch(() => result)
    resultTab.value = 'stdout'
    resultVisible.value = true
    if (result.status === 'success') {
      ElMessage.success('执行成功')
    } else {
      ElMessage.warning(`执行未通过：${runStatusText(result.status)}`)
    }
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '执行失败')
  } finally {
    executingId.value = null
  }
}

const batchBusy = ref(false)

async function batchExecute(rows: Record<string, unknown>[]): Promise<void> {
  if (batchBusy.value) {
    return
  }
  const scripts = rows as unknown as AutomationScript[]
  if (!scripts.length) {
    return
  }
  const prodCount = scripts.filter(
    (row) => envs.value.find((env) => env.id === row.envId)?.kind === 'prod',
  ).length
  if (prodCount > 0) {
    const ok = (await prodDialog.value?.open(
      `选中的脚本里有 ${prodCount} 个绑定了生产环境，确认批量执行？`,
    )) ?? false
    if (!ok) {
      return
    }
  }
  batchBusy.value = true
  try {
    const result = await automationApi.batchRun(scripts.map((row) => Number(row.id)))
    ElMessage.success(
      `已投递 ${result.total} 个脚本到任务队列，可到「任务队列」查看进度`,
    )
    tableRef.value?.clearSelection()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '批量执行失败')
  } finally {
    batchBusy.value = false
  }
}

async function executeAsync(row: AutomationScript): Promise<void> {
  if (executingId.value !== null) {
    return
  }
  if (!(await confirmProdEnv(row))) {
    return
  }
  executingId.value = row.id
  try {
    const result = await automationApi.runAsync(row.id)
    ElMessage.success(`已加入任务队列（#${result.task.id}），可到「任务队列」查看进度`)
    tableRef.value?.reload()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加入队列失败')
  } finally {
    executingId.value = null
  }
}

// ---- 编辑 ----
const editorVisible = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)

const form = reactive({
  name: '',
  framework: '',
  runMode: 'script',
  entryCommand: '',
  timeoutSeconds: 300,
  envId: null as number | null,
  caseMatchMode: 'case_no' as 'case_no' | 'tag',
  caseMatchValue: '',
  description: '',
  content: '',
  changeNote: '',
  preDbRefs: [] as string[],
  postDbRefs: [] as string[],
  dbAssertRefs: [] as string[],
})

/** 新建时的起步正文：保存即可执行，避免空脚本；性能脚本给最小可运行的 JMeter 计划。 */
const STARTER_CONTENT: Record<string, string> = {
  web: '# AITP Web 自动化脚本示例（保存后可直接执行）\n# 需要浏览器操作时，把上方框架切到 Selenium / Playwright 再写用例\nprint("hello from AITP")\n',
  app: '# AITP App 自动化脚本示例（保存后可直接执行）\n# 真机/模拟器步骤需要先启动 Appium 服务（见页面顶部提示）\nprint("hello from AITP")\n',
  api: '# AITP 接口自动化脚本示例（保存后可直接执行）\nprint("hello from AITP")\n',
  performance: `<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="AITP 性能脚本" enabled="true">
      <boolProp name="TestPlan.functional_mode">false</boolProp>
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" testname="用户定义的变量" enabled="true">
        <collectionProp name="Arguments.arguments"/>
      </elementProp>
    </TestPlan>
    <hashTree/>
  </hashTree>
</jmeterTestPlan>
`,
}

/** 新建时的框架默认值：优先选本机可用的常用框架，避免用户漏选后建出不可运行的脚本。 */
const FRAMEWORK_PREFERENCE: Record<string, string[]> = {
  web: ['selenium', 'playwright', 'pytest'],
  app: ['pytest', 'appium', 'uiautomator2', 'hypium'],
  api: ['requests', 'pytest', 'unittest'],
  performance: ['jmeter'],
}

function defaultFramework(): string {
  const available = frameworkChoices.value.map((item) => item.code)
  const preferred = FRAMEWORK_PREFERENCE[category.value] ?? []
  for (const code of preferred) {
    if (available.includes(code)) {
      return code
    }
  }
  // 运行时体检还没回来时也别让框架空着：按类别给静态兜底
  return available[0] ?? preferred[0] ?? ''
}

const sqlScriptOptions = ref<SqlScript[]>([])

async function loadSqlScripts(): Promise<void> {
  try {
    const payload = await sqlScriptApi.list({ pageSize: 100 })
    sqlScriptOptions.value = payload.list ?? []
  } catch {
    sqlScriptOptions.value = []
  }
}

function openCreate(): void {
  editingId.value = null
  form.name = ''
  form.framework = defaultFramework()
  form.runMode = 'script'
  form.entryCommand = ''
  form.timeoutSeconds = runtime.value?.pythonTimeout ?? 300
  form.envId = null
  form.caseMatchMode = 'case_no'
  form.caseMatchValue = ''
  form.description = ''
  form.content = STARTER_CONTENT[category.value] ?? ''
  form.changeNote = ''
  form.preDbRefs = []
  form.postDbRefs = []
  form.dbAssertRefs = []
  void loadSqlScripts()
  editorVisible.value = true
  if (!runtime.value) {
    // 弹框立即打开；运行时体检在后台补，回来后框架中文名与可用项自然补齐
    void automationApi.runtime().then((info) => {
      runtime.value = info
      if (!form.framework) {
        form.framework = defaultFramework()
      }
    }).catch(() => undefined)
  }
}

async function openEdit(row: AutomationScript): Promise<void> {
  try {
    const detail = await automationApi.detail(row.id)
    editingId.value = detail.id
    form.name = detail.name
    form.framework = detail.framework
    form.runMode = detail.runMode === 'pytest' ? 'pytest' : 'script'
    form.entryCommand = detail.entryCommand
    form.timeoutSeconds = detail.timeoutSeconds ?? runtime.value?.pythonTimeout ?? 300
    form.envId = detail.envId ?? null
    form.caseMatchMode = detail.caseMatchMode === 'tag' ? 'tag' : 'case_no'
    form.caseMatchValue = detail.caseMatchValue ?? ''
    form.description = detail.description
    form.content = detail.content
    form.changeNote = ''
    form.preDbRefs = detail.preDbRefs ?? []
    form.postDbRefs = detail.postDbRefs ?? []
    form.dbAssertRefs = detail.dbAssertRefs ?? []
    void loadSqlScripts()
    editorVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载脚本失败')
  }
}

async function saveScript(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请填写脚本名称')
    return
  }
  saving.value = true
  try {
    const body: Record<string, unknown> = {
      projectId: appStore.currentProjectId ?? undefined,
      name: form.name.trim(),
      category: category.value,
      framework: form.framework,
      runMode: category.value === 'performance' ? 'jmeter' : form.runMode,
      entryCommand: form.entryCommand.trim(),
      timeoutSeconds: form.timeoutSeconds,
      envId: form.envId,
      caseMatchMode: form.caseMatchMode,
      caseMatchValue: form.caseMatchValue.trim(),
      description: form.description,
      content: form.content,
      changeNote: form.changeNote.trim(),
      preDbRefs: form.preDbRefs,
      postDbRefs: form.postDbRefs,
      dbAssertRefs: form.dbAssertRefs,
    }
    if (editingId.value) {
      await automationApi.update(editingId.value, body)
      ElMessage.success('已保存')
    } else {
      if (!appStore.currentProjectId) {
        ElMessage.warning('请先选择项目')
        return
      }
      await automationApi.create(body)
      ElMessage.success('已创建')
    }
    editorVisible.value = false
    await reloadAll()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeScript(row: AutomationScript): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除脚本「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await automationApi.remove(row.id)
    ElMessage.success('已删除')
    await reloadAll()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

const copying = ref(false)

async function copyScript(row: AutomationScript): Promise<void> {
  if (copying.value) return
  copying.value = true
  try {
    const copyRow = await automationApi.copy(row.id)
    ElMessage.success(`已复制：${copyRow.scriptNo}（${copyRow.name}）`)
    await reloadAll()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '复制失败')
  } finally {
    copying.value = false
  }
}

// ---- 历史 ----
const historyVisible = ref(false)
const historyRows = ref<AutomationRun[]>([])

async function openHistory(row: AutomationScript): Promise<void> {
  try {
    const payload = await automationApi.runs({ scriptId: row.id, pageSize: 100 })
    historyRows.value = payload.list ?? []
    historyVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载执行历史失败')
  }
}

// ---- P1 #7 版本差异/评审 ----
const appStore = useAppStore()
const versionVisible = ref(false)
const versionScriptId = ref<number | null>(null)
const versions = ref<{ id: number; versionNo: number; changeNote: string; reviewStatus: string; createdAt: string }[]>([])
const diffText = ref('')

async function openVersions(row: AutomationScript): Promise<void> {
  versionScriptId.value = row.id
  diffText.value = ''
  try {
    versions.value = (await automationApi.versions(row.id)).list ?? []
    versionVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载版本失败')
  }
}

async function showDiff(base: string): Promise<void> {
  if (versionScriptId.value == null) return
  try {
    const r = await automationApi.diff(versionScriptId.value, base, 'current')
    diffText.value = (r.diff ?? []).join('\n') || '（无差异）'
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载差异失败')
  }
}

async function review(row: { id: number }, status: string): Promise<void> {
  if (versionScriptId.value == null) return
  await automationApi.reviewVersion(versionScriptId.value, row.id, status)
  ElMessage.success(status === 'approved' ? '已通过' : '已驳回')
  versions.value = (await automationApi.versions(versionScriptId.value)).list ?? []
}

// ---- P1 #8 元素库 ----
const elementVisible = ref(false)
const elements = ref<{ id: number; page: string; name: string; selector: string; kind: string; description: string }[]>([])
const elementForm = reactive({ page: '', name: '', selector: '', kind: 'css' })

async function loadElements(): Promise<void> {
  if (!appStore.currentProjectId) return
  elements.value = (await automationApi.elements(appStore.currentProjectId)).list ?? []
}

async function openElements(): Promise<void> {
  try {
    await loadElements()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载元素库失败')
    return
  }
  elementVisible.value = true
}

async function saveElement(): Promise<void> {
  if (!elementForm.name.trim() || !elementForm.selector.trim()) {
    ElMessage.warning('元素名与选择器必填')
    return
  }
  await automationApi.createElement({ projectId: appStore.currentProjectId, ...elementForm })
  ElMessage.success('已添加')
  Object.assign(elementForm, { page: '', name: '', selector: '', kind: 'css' })
  await loadElements()
}

async function removeElement(row: { id: number }): Promise<void> {
  await automationApi.removeElement(row.id)
  await loadElements()
}

// ---- P2 定时调度 ----
const scheduleVisible = ref(false)
const schedules = ref<Record<string, any>[]>([])
const scheduleForm = reactive({
  name: '',
  targetType: 'scenario' as 'scenario' | 'script',
  targetId: null as number | null,
  intervalMinutes: 60,
})
const scheduleScenarios = ref<ApiScenario[]>([])
const scheduleScripts = ref<AutomationScript[]>([])

const scheduleTargetOptions = computed(() =>
  (scheduleForm.targetType === 'scenario' ? scheduleScenarios.value : scheduleScripts.value).map(
    (item) => {
      const record = item as unknown as Record<string, unknown>
      const no = String(record.scenarioNo ?? record.scriptNo ?? '')
      return { id: Number(item.id), label: `${no} ${item.name}`.trim() }
    },
  ),
)

function scheduleTargetText(row: Record<string, any>): string {
  const kind = row.targetType === 'scenario' ? '场景' : '脚本'
  const id = Number(row.targetId)
  const source = (row.targetType === 'scenario' ? scheduleScenarios.value : scheduleScripts.value) as unknown as Record<string, any>[]
  const hit = source.find((item) => Number(item.id) === id)
  const no = String(hit?.scenarioNo ?? hit?.scriptNo ?? '')
  return no ? `${kind} ${no} ${hit?.name ?? ''}`.trim() : `${kind}#${id}`
}

function onScheduleTypeChange(): void {
  scheduleForm.targetId = null
}

async function loadScheduleTargets(): Promise<void> {
  const [scenarioPage, scriptPage] = await Promise.all([
    apiScenarioApi.list({ projectId: appStore.currentProjectId, pageSize: 200 }),
    automationApi.list({ projectId: appStore.currentProjectId, pageSize: 200 }),
  ])
  scheduleScenarios.value = scenarioPage.list ?? []
  scheduleScripts.value = scriptPage.list ?? []
}

async function loadSchedules(): Promise<void> {
  schedules.value = (await schedulesApi.list({ projectId: appStore.currentProjectId, pageSize: 100 })).list ?? []
}

async function openSchedules(): Promise<void> {
  try {
    await Promise.all([loadSchedules(), loadScheduleTargets()])
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载调度失败')
    return
  }
  Object.assign(scheduleForm, { name: '', targetType: 'scenario', targetId: null, intervalMinutes: 60 })
  scheduleVisible.value = true
}

async function saveSchedule(): Promise<void> {
  if (!scheduleForm.name.trim()) { ElMessage.warning('调度名必填'); return }
  if (scheduleForm.targetId == null) { ElMessage.warning('请选择调度目标'); return }
  await schedulesApi.create({
    projectId: appStore.currentProjectId,
    name: scheduleForm.name.trim(),
    targetType: scheduleForm.targetType,
    targetId: scheduleForm.targetId,
    intervalMinutes: scheduleForm.intervalMinutes,
  })
  ElMessage.success('已添加')
  scheduleForm.name = ''
  await loadSchedules()
}

async function toggleSchedule(row: Record<string, any>, v: boolean): Promise<void> {
  await schedulesApi.update(row.id, { enabled: v })
  await loadSchedules()
}

async function triggerSchedule(row: Record<string, any>): Promise<void> {
  await schedulesApi.trigger(row.id)
  ElMessage.success('已投递执行')
}

async function removeSchedule(row: Record<string, any>): Promise<void> {
  await schedulesApi.remove(row.id)
  await loadSchedules()
}

async function openRunDetail(row: AutomationRun): Promise<void> {
  try {
    lastRun.value = await automationApi.runDetail(row.id)
    resultTab.value = 'stdout'
    resultVisible.value = true
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载执行详情失败')
  }
}

function artifactUrl(runId: number | string, path: string): string {
  return automationApi.artifactUrl(Number(runId), path)
}

/** 是否图片产物：截图是失败复盘的核心，给个直接预览入口而不是只能下载 */
function isImage(path: string): boolean {
  return /\.(png|jpe?g|gif|webp)$/i.test(path || '')
}

const previewVisible = ref(false)
const previewArtifact = ref<{ runId: number | string; path: string } | null>(null)

function openPreview(runId: number | string, path: string): void {
  previewArtifact.value = { runId, path }
  previewVisible.value = true
}

function reportUrl(runId: number | string): string {
  return automationApi.runReportUrl(Number(runId))
}

function formatSize(size: number): string {
  if (!size) {
    return '0 B'
  }
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

// ---- 展示辅助 ----
function runStatusText(status: string): string {
  const labels: Record<string, string> = {
    running: '执行中',
    success: '成功',
    failed: '失败',
    timeout: '超时',
    cancelled: '已取消',
    error: '启动失败',
  }
  return labels[status] || status
}

function runTagType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'success') {
    return 'success'
  }
  if (status === 'failed' || status === 'error') {
    return 'danger'
  }
  if (status === 'timeout') {
    return 'warning'
  }
  return 'info'
}

function writebackTagType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'parsed') {
    return 'success'
  }
  if (status === 'partial') {
    return 'warning'
  }
  if (status === 'fallback' || status === 'failed') {
    return 'danger'
  }
  return 'info'
}

function caseResultText(result: string): string {
  const labels: Record<string, string> = {
    passed: '通过',
    failed: '失败',
    blocked: '阻塞',
    skipped: '跳过',
  }
  return labels[result] || result
}

function caseResultTagType(result: string): 'success' | 'danger' | 'warning' | 'info' {
  if (result === 'passed') {
    return 'success'
  }
  if (result === 'failed') {
    return 'danger'
  }
  if (result === 'blocked') {
    return 'warning'
  }
  return 'info'
}

function mappedByText(mappedBy: string): string {
  const labels: Record<string, string> = {
    case_no: '用例编号',
    script: '脚本整体',
  }
  return labels[mappedBy] || mappedBy || '-'
}

async function reloadAll(): Promise<void> {
  // 先刷列表：保存/删除后用户最先看到的是表格，统计与体检信息随后补齐
  tableRef.value?.reload()
  try {
    categories.value = await automationApi.categories(
      appStore.currentProjectId ? { projectId: appStore.currentProjectId } : {},
    )
    runtime.value = await automationApi.runtime()
  } catch {
    // 体检信息拿不到不阻塞主流程，表格自己会提示加载失败
  }
  try {
    interpreters.value = await automationApi.interpreters(category.value)
  } catch {
    interpreters.value = null
  }
  try {
    envs.value = (await testEnvApi.list()).list
  } catch {
    envs.value = []
  }
}

let activatedOnce = false
onMounted(() => {
  void reloadAll()
})
// 四个二级菜单共用组件、被 keep-alive 按路径缓存：切回来不会重新挂载，不重拉就还是上次的数据。
onActivated(() => {
  // 首次挂载时 onActivated 与 onMounted 同时触发，跳过以免重复请求
  if (activatedOnce) {
    void reloadAll()
  }
  activatedOnce = true
})
// 同一个组件内切类别只变路由参数、不重挂载，onActivated 也不会触发。
watch(category, () => {
  tableRef.value?.clearSelection()
  void reloadAll()
})
// 切项目后列表、统计、元素与调度目标都要按新项目重取
watch(
  () => appStore.currentProjectId,
  () => {
    tableRef.value?.clearSelection()
    void reloadAll()
  },
)
</script>

<style scoped>
.auto-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}

.auto-head__actions {
  display: flex;
  gap: 8px;
}

.auto-alert {
  margin-bottom: 12px;
}

.framework-hint {
  width: 100%;
  margin: 8px 0 0;
  padding: 9px 12px 9px 13px;
  border: 1px solid color-mix(in srgb, var(--aitp-note-warning) 16%, transparent);
  border-left: 3px solid var(--aitp-note-warning);
  border-radius: 10px;
  background: color-mix(in srgb, var(--aitp-note-warning) 5%, var(--aitp-panel));
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--aitp-text-weak);
}

.framework-hint p {
  margin: 2px 0;
  line-height: 1.6;
}

.framework-hint p:first-child {
  font-weight: 600;
  color: var(--aitp-text);
}

.framework-hint__cmd {
  padding: 4px 8px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  word-break: break-all;
}

.framework-hint__alt {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: 4px 0;
}

.version-diff {
  margin-top: 10px;
  max-height: 300px;
  overflow: auto;
  background: var(--aitp-subtle);
  border-radius: 6px;
  padding: 8px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

.element-form {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.auto-artifacts {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.auto-artifacts__row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 10px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.auto-artifacts__path {
  flex: 1;
  word-break: break-all;
}

.auto-artifacts__link {
  text-decoration: none;
}

.auto-report-link {
  margin: 0 12px;
  text-decoration: none;
}

.auto-artifacts__empty {
  margin: 4px 0;
  font-size: 13px;
  line-height: 1.7;
}

/* 产物预览：失败截图是复盘核心，限宽自适应方便直接看 */
.auto-preview {
  text-align: center;
}

.auto-preview img {
  max-width: 100%;
  height: auto;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.env-hint {
  width: 100%;
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.auto-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.auto-block__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.auto-block__head h3 {
  margin: 0;
  font-size: 15px;
}

.auto-name {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.auto-name__title {
  font-weight: 600;
}

.auto-result__head {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.auto-result__cmd {
  padding: 8px 10px;
  margin-bottom: 12px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  word-break: break-all;
  white-space: pre-wrap;
}

.auto-result__out {
  margin: 0;
  padding: 10px 12px;
  max-height: 380px;
  overflow: auto;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
}

.auto-writeback {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 10px;
  margin-bottom: 12px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.auto-writeback__summary {
  flex: 1;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-all;
}

.auto-writeback__table {
  margin-bottom: 14px;
}
</style>
