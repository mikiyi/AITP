<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">AI 评测</h2>
      <div class="toolbar" style="margin: 0">
        <ProjectSwitcher />
        <el-button :icon="Setting" @click="goEvalSettings">AI 评测配置</el-button>
        <el-button :icon="Refresh" @click="refreshActive">刷新</el-button>
        <el-button v-if="activeTab === 'suites'" type="primary" :icon="Plus" @click="openSuiteCreate">新建评测集</el-button>
        <el-button v-else-if="activeTab === 'cases'" type="primary" :icon="Plus" @click="openCaseCreate">新增用例</el-button>
        <el-button v-else-if="activeTab === 'runs'" type="primary" :icon="Plus" @click="openCreateRun">发起跑批</el-button>
        <el-button v-else-if="activeTab === 'gates'" type="primary" :icon="Plus" @click="openGateCreate">新建门禁</el-button>
      </div>
    </div>

    <el-alert
      class="eval-intro"
      type="info"
      :closable="false"
      show-icon
      title="AI 评测做什么：用一套固定的金题集反复跑同一个 AI 能力，量化「换模型、改提示词、换判定器之后它到底变好还是变差」，再把结论收敛成可复核的门禁与报告。"
      description="流程：建评测集 → 维护用例与断言 → 发起跑批（回放模式不烧额度）→ 看 18 项指标与质量面板 → 配门禁阈值求值 → 得到 pass / warn / insufficient / fail 与未达标清单。样本不足时只出证据、不下结论；只有 5 项预注册主指标能判 fail。"
    />

    <div v-if="currentSuiteId && activeTab !== 'suites'" class="panel panel-pad eval-context">
      <span class="muted">当前评测集</span>
      <el-select v-model="currentSuiteId" filterable style="width: 260px" @change="onSuiteChange">
        <el-option v-for="item in suiteOptions" :key="item.id" :label="item.name" :value="item.id" />
      </el-select>
      <el-tag v-if="currentSuite" size="small" effect="plain">
        {{ labelOf(EVAL_TARGET_TYPE_LABELS, currentSuite.targetType) }} · v{{ currentSuite.version }} · {{ currentSuite.caseCount }} 条
      </el-tag>
      <el-tag v-if="currentSuite" size="small" :type="visibilityTagType(currentSuite.visibility)" effect="plain">
        {{ labelOf(EVAL_VISIBILITY_LABELS, currentSuite.visibility) }}
      </el-tag>
      <div class="spacer" />
      <el-button link type="primary" @click="backToSuites">返回评测集列表</el-button>
    </div>

    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <el-tab-pane label="评测集" name="suites">
        <div class="panel panel-pad">
          <div class="toolbar">
            <el-input
              v-model="suiteFilters.keyword"
              placeholder="搜索评测集"
              clearable
              style="width: 200px"
              @input="scheduleSuiteQuery"
              @keyup.enter="reloadSuites"
              @clear="reloadSuites"
            />
            <el-select v-model="suiteFilters.targetType" placeholder="目标" clearable style="width: 130px" @change="reloadSuites">
              <el-option v-for="item in targetTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <el-select v-model="suiteFilters.visibility" placeholder="可见性" clearable style="width: 150px" @change="reloadSuites">
              <el-option v-for="item in visibilityOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <el-select v-model="suiteFilters.status" placeholder="状态" clearable style="width: 120px" @change="reloadSuites">
              <el-option v-for="item in suiteStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </div>

          <el-table v-loading="suiteLoading" :data="suiteRows" border stripe>
            <el-table-column label="名称" min-width="200">
              <template #default="scope">
                <div>{{ scope.row.name }}</div>
                <div v-if="scope.row.description" class="muted ellipsis">{{ scope.row.description }}</div>
              </template>
            </el-table-column>
            <el-table-column label="目标" width="90">
              <template #default="scope">{{ labelOf(EVAL_TARGET_TYPE_LABELS, scope.row.targetType) }}</template>
            </el-table-column>
            <el-table-column label="可见性" width="130">
              <template #default="scope">
                <el-tag size="small" :type="visibilityTagType(scope.row.visibility)" effect="plain">
                  {{ labelOf(EVAL_VISIBILITY_LABELS, scope.row.visibility) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="caseCount" label="用例数" width="90" align="center" />
            <el-table-column label="版本" width="80" align="center">
              <template #default="scope">v{{ scope.row.version }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="scope">
                <el-tag size="small" :type="scope.row.status === 'active' ? 'success' : 'info'" effect="plain">
                  {{ labelOf(EVAL_SUITE_STATUS_LABELS, scope.row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="最近跑批" width="170">
              <template #default="scope">
                <template v-if="scope.row.latestRun">
                  <el-tooltip :content="`${scope.row.latestRun.passed}/${scope.row.latestRun.total} 通过 · ${dateTimeText(scope.row.latestRun.finishedAt || scope.row.latestRun.createdAt)}`">
                    <el-tag size="small" :type="runStatusTagType(scope.row.latestRun.status)" effect="plain">
                      {{ labelOf(EVAL_RUN_STATUS_LABELS, scope.row.latestRun.status) }}
                    </el-tag>
                  </el-tooltip>
                  <span class="muted" style="margin-left: 6px">#{{ scope.row.latestRun.id }}</span>
                </template>
                <span v-else class="muted">未跑批</span>
              </template>
            </el-table-column>
            <el-table-column label="门禁结论" width="110">
              <template #default="scope">
                <el-tag
                  v-if="scope.row.latestRun"
                  size="small"
                  :type="conclusionTagType(scope.row.latestRun.gateConclusion)"
                  effect="plain"
                >
                  {{ labelOf(EVAL_CONCLUSION_LABELS, scope.row.latestRun.gateConclusion) }}
                </el-tag>
                <span v-else class="muted">-</span>
              </template>
            </el-table-column>
            <el-table-column label="更新时间" width="165">
              <template #default="scope">{{ dateTimeText(scope.row.updatedAt) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="250" fixed="right">
              <template #default="scope">
                <el-button link type="primary" @click="enterSuite(scope.row)">进入</el-button>
                <el-button link @click="openSuiteEdit(scope.row)">编辑</el-button>
                <el-button link @click="duplicateSuite(scope.row)">复制</el-button>
                <el-button link type="danger" @click="deleteSuite(scope.row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <span class="muted">还没有评测集。先建一个，接着添加用例或批量导入。</span>
            </template>
          </el-table>

          <div class="pager">
            <el-pagination
              v-model:current-page="suitePage"
              v-model:page-size="suitePageSize"
              :total="suiteTotal"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              @current-change="loadSuites"
              @size-change="reloadSuites"
            />
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="用例" name="cases" :disabled="!currentSuiteId">
        <div class="panel panel-pad">
          <div class="toolbar">
            <el-input
              v-model="caseFilters.keyword"
              placeholder="搜索编号/标题/标签"
              clearable
              style="width: 220px"
              @input="scheduleCaseQuery"
              @keyup.enter="reloadCases"
              @clear="reloadCases"
            />
            <el-select v-model="caseFilters.category" placeholder="分类" clearable style="width: 130px" @change="reloadCases">
              <el-option v-for="item in categoryOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <el-select v-model="caseFilters.severity" placeholder="级别" clearable style="width: 130px" @change="reloadCases">
              <el-option v-for="item in severityOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <el-select v-model="caseFilters.status" placeholder="状态" clearable style="width: 120px" @change="reloadCases">
              <el-option v-for="item in caseStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <div class="spacer" />
            <el-button :icon="Upload" @click="openImport">批量导入</el-button>
          </div>

          <el-table v-loading="caseLoading" :data="caseRows" border stripe>
            <el-table-column prop="caseNo" label="编号" width="90" />
            <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
            <el-table-column label="分类" width="90">
              <template #default="scope">
                <el-tag size="small" effect="plain">{{ labelOf(EVAL_CATEGORY_LABELS, scope.row.category) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="级别" width="90">
              <template #default="scope">
                <el-tag size="small" :type="severityTagType(scope.row.severity)" effect="plain">{{ scope.row.severity }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="难度" width="70" align="center">
              <template #default="scope">{{ EVAL_DIFFICULTY_LABELS[scope.row.difficulty] || scope.row.difficulty }}</template>
            </el-table-column>
            <el-table-column prop="expectedType" label="判定类型" width="100" />
            <el-table-column prop="tags" label="标签" width="140" show-overflow-tooltip />
            <el-table-column label="状态" width="90">
              <template #default="scope">
                <el-tag size="small" :type="scope.row.status === 'active' ? 'success' : 'info'" effect="plain">
                  {{ labelOf(EVAL_CASE_STATUS_LABELS, scope.row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" fixed="right">
              <template #default="scope">
                <el-button link type="primary" @click="openCaseEdit(scope.row)">编辑</el-button>
                <el-button link type="danger" @click="deleteCase(scope.row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <span class="muted">还没有用例。新增一条，或把公开评测样本批量粘贴进来。</span>
            </template>
          </el-table>

          <div class="pager">
            <el-pagination
              v-model:current-page="casePage"
              v-model:page-size="casePageSize"
              :total="caseTotal"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              @current-change="loadCases"
              @size-change="reloadCases"
            />
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="跑批" name="runs" :disabled="!currentSuiteId">
        <div class="panel panel-pad">
          <div class="toolbar">
            <el-select v-model="runFilters.status" placeholder="状态" clearable style="width: 130px" @change="reloadRuns">
              <el-option v-for="item in runStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <el-select v-model="runFilters.gateConclusion" placeholder="门禁结论" clearable style="width: 140px" @change="reloadRuns">
              <el-option v-for="item in conclusionOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </div>

          <el-table v-loading="runLoading" :data="runRows" border stripe>
            <el-table-column label="跑批" width="80">
              <template #default="scope">#{{ scope.row.id }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="scope">
                <el-tag size="small" :type="runStatusTagType(scope.row.status)" effect="plain">
                  {{ labelOf(EVAL_RUN_STATUS_LABELS, scope.row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="用例" width="80" align="center">
              <template #default="scope">{{ scope.row.total }}</template>
            </el-table-column>
            <el-table-column label="通过/失败/跳过/错误" width="170" align="center">
              <template #default="scope">
                {{ scope.row.passed }} / {{ scope.row.failed }} / {{ scope.row.skipped }} / {{ scope.row.errors }}
              </template>
            </el-table-column>
            <el-table-column label="成功率" width="100">
              <template #default="scope">{{ pct(scope.row.successRate) }}</template>
            </el-table-column>
            <el-table-column label="门禁" width="100">
              <template #default="scope">
                <el-tag v-if="scope.row.gateConclusion" size="small" :type="conclusionTagType(scope.row.gateConclusion)" effect="plain">
                  {{ labelOf(EVAL_CONCLUSION_LABELS, scope.row.gateConclusion) }}
                </el-tag>
                <span v-else class="muted">未求值</span>
              </template>
            </el-table-column>
            <el-table-column label="成本" width="110">
              <template #default="scope">{{ money(scope.row.costMicros) }}</template>
            </el-table-column>
            <el-table-column label="创建时间" width="165">
              <template #default="scope">{{ dateTimeText(scope.row.createdAt) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="260" fixed="right">
              <template #default="scope">
                <el-button link type="primary" @click="openRunDetail(scope.row)">详情</el-button>
                <el-button
                  v-if="['queued', 'failed', 'cancelled'].includes(scope.row.status)"
                  link
                  type="success"
                  @click="openReplayFor(scope.row.id)"
                >
                  回放
                </el-button>
                <el-button
                  v-if="['failed', 'cancelled'].includes(scope.row.status)"
                  link
                  @click="resumeRunRow(scope.row)"
                >
                  续跑
                </el-button>
                <el-button
                  v-if="['queued', 'running'].includes(scope.row.status)"
                  link
                  type="warning"
                  @click="cancelRunRow(scope.row)"
                >
                  取消
                </el-button>
                <el-button v-if="scope.row.status === 'finished'" link @click="openCompare(scope.row)">对比</el-button>
                <el-dropdown trigger="click" @command="(cmd: string) => onExportCommand(scope.row, cmd)">
                  <el-button link type="primary">导出</el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="md">Markdown</el-dropdown-item>
                      <el-dropdown-item command="xlsx">Excel</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </template>
            </el-table-column>
            <template #empty>
              <span class="muted">还没有跑批。发起一次并粘贴模型输出，即可零成本回放判定。</span>
            </template>
          </el-table>

          <div class="pager">
            <el-pagination
              v-model:current-page="runPage"
              v-model:page-size="runPageSize"
              :total="runTotal"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              @current-change="loadRuns"
              @size-change="reloadRuns"
            />
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="质量" name="quality" :disabled="!currentSuiteId">
        <div v-loading="qualityLoading" class="panel panel-pad">
          <template v-if="quality">
            <el-descriptions :column="4" border>
              <el-descriptions-item label="用例数">{{ quality.caseCount }}</el-descriptions-item>
              <el-descriptions-item label="覆盖组合">{{ quality.coverage.length }}</el-descriptions-item>
              <el-descriptions-item label="相似用例对">{{ quality.redundancy.duplicates.length }} / {{ quality.redundancy.pairs }}</el-descriptions-item>
              <el-descriptions-item label="标注样本">{{ quality.annotationConsistency.sampleSize }}</el-descriptions-item>
            </el-descriptions>

            <h4 class="quality-title">覆盖矩阵（分类 × 级别）</h4>
            <el-table :data="quality.coverage" size="small" border>
              <el-table-column label="分类" width="120">
                <template #default="scope">{{ labelOf(EVAL_CATEGORY_LABELS, scope.row.category) }}</template>
              </el-table-column>
              <el-table-column prop="severity" label="级别" width="100" />
              <el-table-column prop="count" label="用例数" width="100" align="center" />
              <template #empty><span class="muted">暂无覆盖数据</span></template>
            </el-table>

            <h4 class="quality-title">难度分布</h4>
            <div class="toolbar">
              <el-tag v-for="item in quality.difficulty" :key="item.level" effect="plain">
                {{ item.label }} · {{ item.count }} 条
              </el-tag>
              <span v-if="!quality.difficulty.length" class="muted">暂无数据</span>
            </div>

            <h4 class="quality-title">冗余度（标题 8-gram Jaccard &gt; 0.8）</h4>
            <el-alert
              type="info"
              :closable="false"
              show-icon
              :title="`相似比例 ${pct(quality.redundancy.ratio)}；重复对仅作去重参考，不是硬门禁`"
            />
            <el-table v-if="quality.redundancy.duplicates.length" :data="quality.redundancy.duplicates" size="small" border class="quality-table">
              <el-table-column prop="a" label="用例 A" width="120" />
              <el-table-column prop="b" label="用例 B" width="120" />
              <el-table-column label="相似度" width="120">
                <template #default="scope">{{ pct(scope.row.similarity) }}</template>
              </el-table-column>
            </el-table>

            <h4 class="quality-title">区分度（跨跑次通过率方差）</h4>
            <el-alert
              type="info"
              :closable="false"
              show-icon
              :title="`有多次跑批记录的用例 ${quality.discrimination.casesWithRuns} 条；平均方差 ${quality.discrimination.meanVariance ?? '-'}`"
            />
            <el-table v-if="quality.discrimination.lowDiscrimination.length" :data="quality.discrimination.lowDiscrimination" size="small" border class="quality-table">
              <el-table-column prop="caseNo" label="用例" width="100" />
              <el-table-column label="通过率" width="100">
                <template #default="scope">{{ pct(scope.row.passRate) }}</template>
              </el-table-column>
              <el-table-column prop="variance" label="方差" width="100" />
              <el-table-column prop="runs" label="跑次" width="80" />
            </el-table>
            <div v-else class="muted quality-hint">暂无低区分度用例（需同集多次跑批后才会出现）。</div>

            <h4 class="quality-title">标注一致性</h4>
            <el-descriptions :column="2" border>
              <el-descriptions-item label="Cohen's κ">
                {{ quality.annotationConsistency.kappa ?? '样本不足' }}
              </el-descriptions-item>
              <el-descriptions-item label="说明">
                {{ quality.annotationConsistency.reason || '同一用例两人独立标注时才有 κ' }}
              </el-descriptions-item>
            </el-descriptions>
          </template>
          <el-empty v-else description="暂无质量数据" />
        </div>

        <div class="panel panel-pad">
          <h4 class="quality-title" style="margin-top: 0">最近跑批趋势</h4>
          <el-table :data="qualityTrends" size="small" border>
            <el-table-column label="跑批" width="80">
              <template #default="scope">#{{ scope.row.runId }}</template>
            </el-table-column>
            <el-table-column label="完成时间" width="170">
              <template #default="scope">{{ dateTimeText(scope.row.createdAt) }}</template>
            </el-table-column>
            <el-table-column label="成功率" width="100">
              <template #default="scope">{{ pct(scope.row.successRate) }}</template>
            </el-table-column>
            <el-table-column label="门禁" width="100">
              <template #default="scope">
                <el-tag v-if="scope.row.gateConclusion" size="small" :type="conclusionTagType(scope.row.gateConclusion)" effect="plain">
                  {{ labelOf(EVAL_CONCLUSION_LABELS, scope.row.gateConclusion) }}
                </el-tag>
                <span v-else class="muted">未求值</span>
              </template>
            </el-table-column>
            <el-table-column label="风险加权错误率" width="160">
              <template #default="scope">{{ trendValue(scope.row, 'severity_weighted_error') }}</template>
            </el-table-column>
            <el-table-column label="幻觉率" width="120">
              <template #default="scope">{{ trendValue(scope.row, 'hallucination_rate') }}</template>
            </el-table-column>
            <template #empty><span class="muted">已完成跑批后才会出现趋势</span></template>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="门禁" name="gates" :disabled="!currentSuiteId">
        <div class="panel panel-pad">
          <el-table v-loading="gateLoading" :data="gateRows" border stripe>
            <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip />
            <el-table-column label="规则" min-width="220" show-overflow-tooltip>
              <template #default="scope">{{ gateThresholdSummary(scope.row) }}</template>
            </el-table-column>
            <el-table-column label="硬门禁" width="90" align="center">
              <template #default="scope">
                <el-tag size="small" :type="scope.row.isHard ? 'danger' : 'info'" effect="plain">
                  {{ scope.row.isHard ? '硬' : '软' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="80" align="center">
              <template #default="scope">
                <el-switch v-model="scope.row.enabled" @change="toggleGate(scope.row)" />
              </template>
            </el-table-column>
            <el-table-column label="上次结论" width="120">
              <template #default="scope">
                <el-tag v-if="scope.row.lastConclusion" size="small" :type="conclusionTagType(scope.row.lastConclusion)" effect="plain">
                  {{ labelOf(EVAL_CONCLUSION_LABELS, scope.row.lastConclusion) }}
                </el-tag>
                <span v-else class="muted">未求值</span>
              </template>
            </el-table-column>
            <el-table-column label="上次跑批" width="100">
              <template #default="scope">
                <span v-if="scope.row.lastRunId">#{{ scope.row.lastRunId }}</span>
                <span v-else class="muted">-</span>
              </template>
            </el-table-column>
            <el-table-column label="求值时间" width="165">
              <template #default="scope">{{ dateTimeText(scope.row.lastEvaluatedAt) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="scope">
                <el-button link type="primary" @click="openGateEvaluate(scope.row)">求值</el-button>
                <el-button link @click="openGateEdit(scope.row)">编辑</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <span class="muted">还没有门禁。新建后可对已完成跑批求值，区间下界判定更严格。</span>
            </template>
          </el-table>
        </div>
      </el-tab-pane>

      </el-tabs>

    <!-- 评测集新建 / 编辑 -->
    <el-dialog v-model="suiteDialog" :title="suiteForm.id ? '编辑评测集' : '新建评测集'" width="600px">
      <el-form label-width="96px">
        <el-form-item label="名称" required>
          <el-input v-model="suiteForm.name" placeholder="例如：金融问答金题集" />
        </el-form-item>
        <el-form-item label="目标类型">
          <el-select v-model="suiteForm.targetType" style="width: 100%">
            <el-option v-for="item in targetTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="可见性">
          <el-select v-model="suiteForm.visibility" style="width: 100%">
            <el-option v-for="item in visibilityOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <div class="muted form-hint">holdout 集用于防污染，禁止导出（160107）。</div>
        </el-form-item>
        <el-form-item v-if="suiteForm.id" label="状态">
          <el-radio-group v-model="suiteForm.status">
            <el-radio value="active">启用</el-radio>
            <el-radio value="archived">归档</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="suiteForm.description" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="suiteForm.tags" placeholder="逗号分隔" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="suiteDialog = false">取消</el-button>
        <el-button type="primary" :loading="suiteSaving" @click="saveSuite">保存</el-button>
      </template>
    </el-dialog>

    <!-- 用例新建 / 编辑 -->
    <el-dialog v-model="caseDialog" :title="caseForm.id ? '编辑用例' : '新增用例'" width="760px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="MVP 要求至少一条断言；已进入跑批结果的用例会冻结，需复制评测集出新版本。"
        style="margin-bottom: 12px"
      />
      <el-form label-width="96px">
        <el-form-item label="标题" required>
          <el-input v-model="caseForm.title" />
        </el-form-item>
        <div class="form-row">
          <el-form-item label="分类">
            <el-select v-model="caseForm.category" style="width: 100%">
              <el-option v-for="item in categoryOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="级别">
            <el-select v-model="caseForm.severity" style="width: 100%">
              <el-option v-for="item in severityOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="难度">
            <el-select v-model="caseForm.difficulty" style="width: 100%">
              <el-option :value="1" label="易" />
              <el-option :value="2" label="中" />
              <el-option :value="3" label="难" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="判定类型">
          <el-select v-model="caseForm.expectedType" style="width: 100%">
            <el-option value="assert" label="assert（规则断言）" />
            <el-option value="rubric" label="rubric（评分表）" />
          </el-select>
        </el-form-item>
        <el-form-item label="输入 JSON">
          <el-input v-model="caseForm.inputText" type="textarea" :rows="3" class="json-area" placeholder='{"question": "..."}' />
        </el-form-item>
        <el-form-item label="断言 JSON">
          <el-input v-model="caseForm.assertionsText" type="textarea" :rows="4" class="json-area" placeholder='{"must_contain": ["..."], "max_chars": 4000}' />
          <div class="muted form-hint">支持 must_contain / forbidden_contains / regex / max_chars / json_keys / grounded / end_state / max_steps / expected_min / should_refuse。</div>
        </el-form-item>
        <el-form-item label="Rubric JSON">
          <el-input v-model="caseForm.rubricText" type="textarea" :rows="3" class="json-area" placeholder='[{"key": "accuracy", "weight": 1, "must_pass": true}]' />
        </el-form-item>
        <el-form-item label="标准答案">
          <el-input v-model="caseForm.expectedText" type="textarea" :rows="2" />
        </el-form-item>
        <div class="form-row">
          <el-form-item label="来源">
            <el-input v-model="caseForm.source" placeholder="manual / badcase / import" />
          </el-form-item>
          <el-form-item label="来源引用">
            <el-input v-model="caseForm.sourceRef" placeholder="aiRecord:12 / bug:34" />
          </el-form-item>
        </div>
        <el-form-item label="标签">
          <el-input v-model="caseForm.tags" placeholder="逗号分隔" />
        </el-form-item>
        <el-form-item v-if="caseForm.id" label="状态">
          <el-radio-group v-model="caseForm.status">
            <el-radio value="active">启用</el-radio>
            <el-radio value="archived">归档</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="caseDialog = false">取消</el-button>
        <el-button type="primary" :loading="caseSaving" @click="saveCase">保存</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入 -->
    <el-dialog v-model="importDialog" title="批量导入用例" width="760px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="粘贴 JSON 数组。先干跑预览可看接受/跳过；正式导入按标题幂等，一次最多 500 条。"
        style="margin-bottom: 12px"
      />
      <el-input
        v-model="importText"
        type="textarea"
        :rows="12"
        class="json-area"
        placeholder='[{"title": "退款到账时效", "category": "golden", "severity": "S1", "assertions": {"must_contain": ["T+1"]}}]'
      />
      <div v-if="importResult" class="import-result">
        <el-alert
          :type="importResult.skipped ? 'warning' : 'success'"
          :closable="false"
          show-icon
          :title="`${importResult.dryRun ? '干跑' : '导入'}结果：接受 ${importResult.accepted} 条，跳过 ${importResult.skipped} 条`"
        />
        <el-table v-if="importResult.skippedItems.length" :data="importResult.skippedItems" size="small" border class="quality-table">
          <el-table-column prop="index" label="#" width="60" />
          <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
          <el-table-column prop="reason" label="原因" min-width="140" />
        </el-table>
      </div>
      <template #footer>
        <el-button @click="importDialog = false">取消</el-button>
        <el-button :loading="importing" @click="dryRunImport">干跑预览</el-button>
        <el-button type="primary" :loading="importing" @click="submitImport">正式导入</el-button>
      </template>
    </el-dialog>

    <!-- 发起跑批 -->
    <el-dialog v-model="runDialog" title="发起跑批" width="620px">
      <el-form label-width="96px">
        <el-form-item label="评测集">
          <span>{{ currentSuite?.name }}（{{ currentSuite?.caseCount }} 条用例，v{{ currentSuite?.version }}）</span>
        </el-form-item>
        <el-form-item label="评测配置">
          <el-select v-model="runForm.configId" clearable placeholder="不选则用调用时传入的参数" style="width: 100%">
            <el-option
              v-for="item in configRows"
              :key="item.id"
              :label="`${item.name}${item.isBaseline ? '（基线）' : ''}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="判定器">
          <el-select v-model="runForm.judgeId" clearable placeholder="不选则只走规则断言" style="width: 100%">
            <el-option
              v-for="item in judgeRows"
              :key="item.id"
              :label="`${item.name} · ${labelOf(EVAL_JUDGE_STRATEGY_LABELS, item.strategy)}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="目的">
          <el-input v-model="runForm.purpose" placeholder="run / regression / ablation" />
        </el-form-item>
        <el-form-item label="消融组">
          <el-input v-model="runForm.ablationGroup" placeholder="同组才允许对比" />
        </el-form-item>
        <el-form-item label="触发来源">
          <el-radio-group v-model="runForm.triggerType">
            <el-radio value="manual">手工</el-radio>
            <el-radio value="ci">CI</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="!runForm.configId" label="温度">
          <el-input-number v-model="runForm.temperature" :min="0" :max="2" :step="0.1" style="width: 160px" />
        </el-form-item>
        <el-form-item v-if="!runForm.configId" label="随机种子">
          <el-input-number v-model="runForm.seed" :min="0" :step="1" controls-position="right" style="width: 160px" />
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="同一用例集 + 配置 + 判定器 + 种子重复发起会命中幂等键，直接复用既有跑批，不重复烧钱。"
        style="margin-top: 8px"
      />
      <template #footer>
        <el-button @click="runDialog = false">取消</el-button>
        <el-button type="primary" :loading="runCreating" @click="submitRun">创建跑批</el-button>
      </template>
    </el-dialog>

    <!-- 回放执行 -->
    <el-dialog v-model="replayDialog" title="回放执行（零 token 判定）" width="760px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="粘贴逐条模型输出（JSON 数组，按 caseNo / caseId 匹配）；没给的用例会记为跳过，不会拖垮整批。"
        style="margin-bottom: 12px"
      />
      <el-input
        v-model="replayForm.text"
        type="textarea"
        :rows="12"
        class="json-area"
        placeholder='[{"caseNo": "EC-001", "output": "模型回答", "latencyMs": 320, "tokensIn": 120, "tokensOut": 80}]'
      />
      <template #footer>
        <el-button @click="replayDialog = false">取消</el-button>
        <el-button type="primary" :loading="replaying" @click="submitReplay">执行判定</el-button>
      </template>
    </el-dialog>

    <!-- 跑批详情 -->
    <el-drawer v-model="runDetailVisible" title="跑批详情" size="82%">
      <div v-loading="runDetailLoading">
        <template v-if="runDetail">
          <el-descriptions :column="4" border>
            <el-descriptions-item label="跑批">#{{ runDetail.run.id }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag size="small" :type="runStatusTagType(runDetail.run.status)" effect="plain">
                {{ labelOf(EVAL_RUN_STATUS_LABELS, runDetail.run.status) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="成功率">{{ pct(runDetail.run.successRate) }}</el-descriptions-item>
            <el-descriptions-item label="门禁">
              <el-tag v-if="runDetail.run.gateConclusion" size="small" :type="conclusionTagType(runDetail.run.gateConclusion)" effect="plain">
                {{ labelOf(EVAL_CONCLUSION_LABELS, runDetail.run.gateConclusion) }}
              </el-tag>
              <span v-else class="muted">未求值</span>
            </el-descriptions-item>
            <el-descriptions-item label="用例数">{{ runDetail.run.total }}</el-descriptions-item>
            <el-descriptions-item label="通过/失败/跳过/错误">
              {{ runDetail.run.passed }} / {{ runDetail.run.failed }} / {{ runDetail.run.skipped }} / {{ runDetail.run.errors }}
            </el-descriptions-item>
            <el-descriptions-item label="成本">{{ money(runDetail.run.costMicros) }}（{{ runDetail.run.costSource }}）</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ dateTimeText(runDetail.run.createdAt) }}</el-descriptions-item>
          </el-descriptions>

          <div class="toolbar" style="margin: 12px 0">
            <el-button
              v-if="['queued', 'failed', 'cancelled'].includes(runDetail.run.status)"
              type="primary"
              @click="openReplayFor(runDetail.run.id)"
            >
              回放执行
            </el-button>
            <el-button
              v-if="['failed', 'cancelled'].includes(runDetail.run.status)"
              @click="resumeRunRow(runDetail.run)"
            >
              续跑
            </el-button>
            <el-button
              v-if="['queued', 'running'].includes(runDetail.run.status)"
              type="warning"
              plain
              @click="cancelRunRow(runDetail.run)"
            >
              取消
            </el-button>
            <el-button :disabled="runDetail.run.status !== 'finished'" @click="openCompare(runDetail.run)">对比基线</el-button>
            <el-dropdown trigger="click" @command="exportDetailRun">
              <el-button :icon="Download">导出</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="md">Markdown</el-dropdown-item>
                  <el-dropdown-item command="xlsx">Excel</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <div class="spacer" />
            <el-button :disabled="!hasHumanDraft" @click="saveHumanScores">保存人评</el-button>
          </div>

          <h4 class="quality-title">18 项指标（Wilson 95% 区间）</h4>
          <el-table :data="runMetricRows" size="small" border>
            <el-table-column label="指标" min-width="180">
              <template #default="scope">
                <span>{{ scope.row.label }}</span>
                <el-tag v-if="scope.row.primary" size="small" type="danger" effect="plain" class="primary-tag">主</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="value" label="值" width="110" />
            <el-table-column prop="ci" label="95% CI" width="170" />
            <el-table-column prop="sample" label="样本" width="80" align="center" />
            <el-table-column prop="description" label="口径" min-width="220" show-overflow-tooltip />
            <template #empty><span class="muted">还没有指标（先回放执行）</span></template>
          </el-table>

          <h4 class="quality-title">逐条结果</h4>
          <el-table :data="runDetail.results" size="small" border>
            <el-table-column type="expand">
              <template #default="scope">
                <div class="result-expand">
                  <div><strong>输出</strong><pre>{{ scope.row.output_text || '-' }}</pre></div>
                  <div><strong>失败原因</strong><pre>{{ scope.row.failure_reason || '-' }}</pre></div>
                  <div><strong>工具轨迹</strong><pre>{{ scope.row.tool_trace }}</pre></div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="case_no" label="用例" width="90" />
            <el-table-column label="标题" min-width="180" show-overflow-tooltip>
              <template #default="scope">{{ scope.row.caseTitle || '-' }}</template>
            </el-table-column>
            <el-table-column label="级别" width="70">
              <template #default="scope">{{ scope.row.severity || '-' }}</template>
            </el-table-column>
            <el-table-column label="结论" width="90">
              <template #default="scope">
                <el-tag size="small" :type="verdictTagType(scope.row.verdict)" effect="plain">
                  {{ labelOf(EVAL_VERDICT_LABELS, scope.row.verdict) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="失败原因" width="160" show-overflow-tooltip>
              <template #default="scope">{{ evalFailureText(scope.row.failure_reason) }}</template>
            </el-table-column>
            <el-table-column label="分数" width="80">
              <template #default="scope">{{ scope.row.score ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="耗时" width="90">
              <template #default="scope">{{ scope.row.latency_ms }} ms</template>
            </el-table-column>
            <el-table-column label="Tokens" width="110">
              <template #default="scope">{{ scope.row.tokens_in }} / {{ scope.row.tokens_out }}</template>
            </el-table-column>
            <el-table-column label="成本" width="90">
              <template #default="scope">{{ money(scope.row.cost_micros) }}</template>
            </el-table-column>
            <el-table-column label="人评" width="120">
              <template #default="scope">
                <el-select v-model="humanDrafts[scope.row.id]" size="small" clearable placeholder="回填">
                  <el-option label="通过" value="pass" />
                  <el-option label="未通过" value="fail" />
                </el-select>
              </template>
            </el-table-column>
            <template #empty><span class="muted">还没有逐条结果</span></template>
          </el-table>
        </template>
      </div>
    </el-drawer>

    <!-- 消融对比 -->
    <el-dialog v-model="compareDialog" title="跑批对比（配对 McNemar）" width="860px">
      <el-form inline label-width="90px">
        <el-form-item label="基线跑批">
          <el-select v-model="compareForm.baselineRunId" placeholder="选择基线" style="width: 200px">
            <el-option v-for="item in suiteRunOptions" :key="item.id" :label="`#${item.id} · ${pct(item.successRate)}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="当前跑批">
          <el-select v-model="compareForm.currentRunId" placeholder="选择当前" style="width: 200px">
            <el-option v-for="item in suiteRunOptions" :key="item.id" :label="`#${item.id} · ${pct(item.successRate)}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="α">
          <el-input-number v-model="compareForm.alpha" :min="0.01" :max="0.2" :step="0.01" style="width: 120px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="compareLoading" @click="submitCompare">开始对比</el-button>
        </el-form-item>
      </el-form>

      <template v-if="compareResult">
        <el-alert
          :type="compareResult.paired.hardAssertionRegression ? 'warning' : 'info'"
          :closable="false"
          show-icon
          :title="`翻转变差 ${compareResult.paired.regressedCount} 条，变好 ${compareResult.paired.improvedCount} 条；McNemar p=${compareResult.paired.pValue.toFixed(4)}，${compareResult.paired.significant ? '显著' : '不显著'}`"
        />
        <el-alert
          v-if="compareResult.paired.hardAssertionRegression"
          type="warning"
          :closable="false"
          show-icon
          title="存在硬断言正向→反向翻转，按设计直接计为回归，不依赖样本量。"
        />
        <el-table :data="compareResult.metrics" size="small" border class="compare-table">
          <el-table-column label="指标" min-width="170">
            <template #default="scope">{{ EVAL_METRIC_LABELS[scope.row.key] || scope.row.key }}</template>
          </el-table-column>
          <el-table-column label="基线" width="110">
            <template #default="scope">{{ metricTextByKey(scope.row.key, scope.row.baseline) }}</template>
          </el-table-column>
          <el-table-column label="当前" width="110">
            <template #default="scope">{{ metricTextByKey(scope.row.key, scope.row.current) }}</template>
          </el-table-column>
          <el-table-column label="Δ" width="120">
            <template #default="scope">
              <span :class="deltaClass(scope.row.delta)">{{ deltaText(scope.row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="样本" width="80" align="center">
            <template #default="scope">{{ scope.row.sampleSize }}</template>
          </el-table-column>
        </el-table>
      </template>
    </el-dialog>

    <!-- 门禁新建 / 编辑 -->
    <el-dialog v-model="gateDialog" :title="gateForm.id ? '编辑门禁' : '新建门禁'" width="760px">
      <el-form label-width="110px">
        <el-form-item label="名称" required>
          <el-input v-model="gateForm.name" placeholder="例如：金融问答发布门禁" />
        </el-form-item>
        <div class="form-row">
          <el-form-item label="探索最小样本">
            <el-input-number v-model="gateForm.minExplore" :min="1" style="width: 140px" />
          </el-form-item>
          <el-form-item label="发布最小样本">
            <el-input-number v-model="gateForm.minPublish" :min="1" style="width: 140px" />
          </el-form-item>
          <el-form-item label="硬门禁">
            <el-switch v-model="gateForm.isHard" />
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="gateForm.enabled" />
          </el-form-item>
        </div>
        <el-form-item label="基线跑批">
          <el-select v-model="gateForm.baselineRunId" clearable placeholder="不设基线" style="width: 100%">
            <el-option v-for="item in suiteRunOptions" :key="item.id" :label="`#${item.id} · ${pct(item.successRate)}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="指标规则">
          <div class="gate-rules">
            <div v-for="rule in gateForm.rules" :key="rule.thresholdKey" class="gate-rule">
              <el-checkbox v-model="rule.enabled">{{ rule.label }}</el-checkbox>
              <el-input-number
                v-model="rule.limit"
                :disabled="!rule.enabled"
                :step="rule.direction === 'min' ? 0.01 : 0.01"
                :min="0"
                style="width: 150px"
              />
              <span class="muted">{{ rule.direction === 'min' ? '≥ 下界' : '≤ 上界' }}</span>
              <el-tag v-if="rule.primary" size="small" type="danger" effect="plain">主指标</el-tag>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="只有 5 个预注册主指标能把结论判为 fail；其余指标越线只降为 warn（族错误率控制）。min 类看 Wilson 下界，max 类看 Wilson 上界。"
      />
      <template #footer>
        <el-button @click="gateDialog = false">取消</el-button>
        <el-button type="primary" :loading="gateSaving" @click="saveGate">保存</el-button>
      </template>
    </el-dialog>

    <!-- 门禁求值 -->
    <el-dialog v-model="gateEvalDialog" title="门禁求值" width="540px">
      <el-form label-width="90px">
        <el-form-item label="门禁">
          <span>{{ gateEvalTarget?.name }}</span>
        </el-form-item>
        <el-form-item label="跑批">
          <el-select v-model="gateEvalRunId" placeholder="选择已完成跑批" style="width: 100%">
            <el-option v-for="item in suiteRunOptions" :key="item.id" :label="`#${item.id} · 成功率 ${pct(item.successRate)}`" :value="item.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <div v-if="!suiteRunOptions.length" class="muted">该评测集还没有已完成跑批，先去跑批页执行一次。</div>
      <template #footer>
        <el-button @click="gateEvalDialog = false">取消</el-button>
        <el-button type="primary" :loading="gateEvaluating" :disabled="!gateEvalRunId" @click="submitGateEvaluation">开始求值</el-button>
      </template>
    </el-dialog>

    <!-- 门禁求值结果 -->
    <el-dialog v-model="gateResultDialog" title="门禁结论" width="860px">
      <template v-if="gateResult">
        <el-alert
          :type="gateResult.conclusion === 'pass' ? 'success' : gateResult.conclusion === 'fail' ? 'error' : 'warning'"
          :closable="false"
          show-icon
          :title="`结论：${labelOf(EVAL_CONCLUSION_LABELS, gateResult.conclusion)}；有效样本 ${gateResult.sampleSize}（探索 ${gateResult.minExplore} / 发布 ${gateResult.minPublish}）`"
        />
        <ul class="gate-notes">
          <li v-for="(note, index) in gateResult.notes" :key="index">{{ note }}</li>
        </ul>
        <el-table :data="gateResult.checks" size="small" border>
          <el-table-column label="指标" min-width="170">
            <template #default="scope">
              {{ EVAL_METRIC_LABELS[scope.row.metric] || scope.row.key }}
              <el-tag v-if="scope.row.primary" size="small" type="danger" effect="plain" class="primary-tag">主</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="值" width="110">
            <template #default="scope">{{ metricTextByKey(scope.row.metric, scope.row.value) }}</template>
          </el-table-column>
          <el-table-column label="判定值" width="120">
            <template #default="scope">
              <el-tooltip :content="judgeBasisText(scope.row.basis)" placement="top">
                <span>{{ metricTextByKey(scope.row.metric, scope.row.judged) }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="规则" width="130">
            <template #default="scope">{{ gateRuleText(scope.row) }}</template>
          </el-table-column>
          <el-table-column label="结果" width="90">
            <template #default="scope">
              <el-tag size="small" :type="scope.row.passed ? 'success' : 'danger'" effect="plain">
                {{ scope.row.passed ? '达标' : '越线' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="备注" min-width="180" show-overflow-tooltip />
        </el-table>
      </template>
    </el-dialog>

    <!-- 配置新建 / 编辑 -->
    <!-- 判定器校准 -->
    <!-- 校准历史 -->
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Plus, Refresh, Setting, Upload } from '@element-plus/icons-vue'
import { evalApi } from '@/api/eval'
import type {
  EvalCaseDetail,
  EvalCompareResult,
  EvalConfig,
  EvalGate,
  EvalGateEvaluation,
  EvalImportResult,
  EvalJudge,
  EvalReplayOutput,
  EvalResultRow,
  EvalRun,
  EvalRunDetail,
  EvalSuite,
  EvalSuiteQuality,
  EvalTrendPoint,
} from '@/api/eval'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import ProjectSwitcher from '@/components/ProjectSwitcher.vue'
import { dateTimeText } from '@/utils/format'
import { useAutoQuery } from '@/composables/useAutoQuery'
import { useProjectScope } from '@/composables/useProjectScope'
import {
  EVAL_CASE_STATUS_LABELS,
  EVAL_CATEGORY_LABELS,
  EVAL_CONCLUSION_LABELS,
  EVAL_DIFFICULTY_LABELS,
  EVAL_GATE_RULES,
  EVAL_JUDGE_STRATEGY_LABELS,
  EVAL_METRIC_LABELS,
  EVAL_METRICS,
  EVAL_METRIC_UNITS,
  EVAL_RUN_STATUS_LABELS,
  EVAL_SEVERITY_LABELS,
  EVAL_SUITE_STATUS_LABELS,
  EVAL_TARGET_TYPE_LABELS,
  EVAL_VERDICT_LABELS,
  EVAL_VISIBILITY_LABELS,
  evalFailureText,
} from '@/constants/eval'

const appStore = useAppStore()
const activeTab = ref('suites')
const routeEval = useRoute()
const routerEval = useRouter()

function optionsOf(map: Record<string, string>): Array<{ value: string; label: string }> {
  return Object.entries(map).map(([value, label]) => ({ value, label }))
}

const targetTypeOptions = optionsOf(EVAL_TARGET_TYPE_LABELS)
const visibilityOptions = optionsOf(EVAL_VISIBILITY_LABELS)
const suiteStatusOptions = optionsOf(EVAL_SUITE_STATUS_LABELS)
const caseStatusOptions = optionsOf(EVAL_CASE_STATUS_LABELS)
const categoryOptions = optionsOf(EVAL_CATEGORY_LABELS)
const severityOptions = optionsOf(EVAL_SEVERITY_LABELS)
const runStatusOptions = optionsOf(EVAL_RUN_STATUS_LABELS)
const conclusionOptions = optionsOf(EVAL_CONCLUSION_LABELS)

function labelOf(map: Record<string, string>, value: string): string {
  return map[value] || value || '-'
}

function pct(value: number | null | undefined): string {
  return typeof value === 'number' ? `${(value * 100).toFixed(2)}%` : '-'
}

function money(micros: number | null | undefined): string {
  if (typeof micros !== 'number' || !micros) {
    return '0'
  }
  return `${(micros / 1_000_000).toFixed(4)} 元`
}

function conclusionTagType(value: string): 'success' | 'warning' | 'info' | 'danger' {
  if (value === 'pass') return 'success'
  if (value === 'fail') return 'danger'
  if (value === 'warn') return 'warning'
  return 'info'
}

function verdictTagType(value: string): 'success' | 'warning' | 'info' | 'danger' {
  if (value === 'pass') return 'success'
  if (value === 'fail') return 'danger'
  if (value === 'error') return 'warning'
  return 'info'
}

function runStatusTagType(value: string): 'success' | 'warning' | 'info' | 'danger' {
  if (value === 'finished') return 'success'
  if (value === 'running') return 'warning'
  if (value === 'failed') return 'danger'
  return 'info'
}

function severityTagType(value: string): 'warning' | 'info' | 'danger' {
  if (value === 'S0') return 'danger'
  if (value === 'S1') return 'warning'
  return 'info'
}

function visibilityTagType(value: string): 'warning' | 'info' {
  return value === 'holdout' ? 'warning' : 'info'
}

// ---------------------------------------------------------------- 评测集

const suiteRows = ref<EvalSuite[]>([])
const suiteOptions = ref<EvalSuite[]>([])
const suiteTotal = ref(0)
const suitePage = ref(1)
const suitePageSize = ref(20)
const suiteLoading = ref(false)
const suiteFilters = reactive({ keyword: '', targetType: '', visibility: '', status: '' })
const suiteDialog = ref(false)
const suiteSaving = ref(false)
const suiteForm = reactive({
  id: null as number | null,
  name: '',
  targetType: 'llm',
  visibility: 'private',
  status: 'active',
  description: '',
  tags: '',
})

async function loadSuites(): Promise<void> {
  suiteLoading.value = true
  try {
    const page = await evalApi.suites({
      keyword: suiteFilters.keyword,
      targetType: suiteFilters.targetType,
      visibility: suiteFilters.visibility,
      status: suiteFilters.status,
      projectId: appStore.currentProjectId ?? undefined,
      pageNum: suitePage.value,
      pageSize: suitePageSize.value,
    })
    suiteRows.value = page.list ?? []
    suiteTotal.value = page.total ?? 0
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载评测集失败')
  } finally {
    suiteLoading.value = false
  }
}

function reloadSuites(): void {
  suitePage.value = 1
  void loadSuites()
}

const scheduleSuiteQuery = useAutoQuery(reloadSuites)

async function loadSuiteOptions(): Promise<void> {
  try {
    const page = await evalApi.suites({
      projectId: appStore.currentProjectId ?? undefined,
      pageNum: 1,
      pageSize: 100,
    })
    suiteOptions.value = page.list ?? []
    if (currentSuiteId.value && !suiteOptions.value.some((item) => item.id === currentSuiteId.value)) {
      currentSuiteId.value = null
      activeTab.value = 'suites'
    }
  } catch {
    suiteOptions.value = []
  }
}

const currentSuiteId = ref<number | null>(null)
const currentSuite = computed(
  () => suiteOptions.value.find((item) => item.id === currentSuiteId.value) ?? null,
)

function openSuiteCreate(): void {
  suiteForm.id = null
  suiteForm.name = ''
  suiteForm.targetType = 'llm'
  suiteForm.visibility = 'private'
  suiteForm.status = 'active'
  suiteForm.description = ''
  suiteForm.tags = ''
  suiteDialog.value = true
}

function openSuiteEdit(row: EvalSuite): void {
  suiteForm.id = row.id
  suiteForm.name = row.name
  suiteForm.targetType = row.targetType
  suiteForm.visibility = row.visibility
  suiteForm.status = row.status
  suiteForm.description = row.description
  suiteForm.tags = row.tags
  suiteDialog.value = true
}

async function saveSuite(): Promise<void> {
  if (!suiteForm.name.trim()) {
    ElMessage.warning('评测集名称不能为空')
    return
  }
  suiteSaving.value = true
  try {
    if (suiteForm.id) {
      await evalApi.updateSuite(suiteForm.id, {
        name: suiteForm.name,
        targetType: suiteForm.targetType,
        visibility: suiteForm.visibility,
        status: suiteForm.status,
        description: suiteForm.description,
        tags: suiteForm.tags,
      })
    } else {
      if (!appStore.currentProjectId) {
        ElMessage.warning('请先选择项目')
        return
      }
      await evalApi.createSuite({
        projectId: appStore.currentProjectId,
        name: suiteForm.name,
        targetType: suiteForm.targetType,
        visibility: suiteForm.visibility,
        description: suiteForm.description,
        tags: suiteForm.tags,
        creator: '本地用户',
      })
    }
    ElMessage.success('已保存')
    suiteDialog.value = false
    await loadSuites()
    await loadSuiteOptions()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    suiteSaving.value = false
  }
}

async function duplicateSuite(row: EvalSuite): Promise<void> {
  try {
    const result = await evalApi.duplicateSuite(row.id, { creator: '本地用户' })
    ElMessage.success(`已复制为「${result.name}」v${result.version}，${result.caseCount} 条用例`)
    await loadSuites()
    await loadSuiteOptions()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '复制失败')
  }
}

async function deleteSuite(row: EvalSuite): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除评测集「${row.name}」？已有跑批会被拒绝。`, '删除确认', {
      type: 'warning',
    })
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    return
  }
  try {
    await evalApi.removeSuite(row.id)
    ElMessage.success('已删除')
    if (currentSuiteId.value === row.id) {
      currentSuiteId.value = null
      activeTab.value = 'suites'
    }
    await loadSuites()
    await loadSuiteOptions()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

function enterSuite(row: EvalSuite): void {
  currentSuiteId.value = row.id
  activeTab.value = 'cases'
  caseFilters.keyword = ''
  caseFilters.category = ''
  caseFilters.severity = ''
  caseFilters.status = ''
  runFilters.status = ''
  runFilters.gateConclusion = ''
  suiteRunOptions.value = []
  quality.value = null
  qualityTrends.value = []
  gateRows.value = []
  void loadCases()
  void loadRuns()
  void loadGates()
  void loadSuiteOptions()
}

function onSuiteChange(): void {
  casePage.value = 1
  runPage.value = 1
  caseRows.value = []
  runRows.value = []
  gateRows.value = []
  suiteRunOptions.value = []
  quality.value = null
  qualityTrends.value = []
  if (activeTab.value === 'cases') void loadCases()
  if (activeTab.value === 'runs') void loadRuns()
  if (activeTab.value === 'quality') void loadQuality()
  if (activeTab.value === 'gates') void loadGates()
}

function onTabChange(name: string | number): void {
  if (name === 'suites') {
    void loadSuites()
    void loadSuiteOptions()
  }
  if (name === 'cases' && currentSuiteId.value) void loadCases()
  if (name === 'runs' && currentSuiteId.value) void loadRuns()
  if (name === 'quality' && currentSuiteId.value) void loadQuality()
  if (name === 'gates' && currentSuiteId.value) void loadGates()
}

function backToSuites(): void {
  activeTab.value = 'suites'
  void loadSuites()
  void loadSuiteOptions()
}

function goEvalSettings(): void {
  void routerEval.push({ path: '/settings/ai', query: { tab: 'eval-config', from: routeEval.fullPath } })
}

function refreshActive(): void {
  if (activeTab.value === 'suites') {
    void loadSuites()
    void loadSuiteOptions()
    return
  }
  if (activeTab.value === 'cases') void loadCases()
  if (activeTab.value === 'runs') void loadRuns()
  if (activeTab.value === 'quality') void loadQuality()
  if (activeTab.value === 'gates') void loadGates()
}

// ---------------------------------------------------------------- 用例

const caseRows = ref<EvalCaseDetail[]>([])
const caseTotal = ref(0)
const casePage = ref(1)
const casePageSize = ref(20)
const caseLoading = ref(false)
const caseFilters = reactive({ keyword: '', category: '', severity: '', status: '' })
const caseDialog = ref(false)
const caseSaving = ref(false)
const caseForm = reactive({
  id: null as number | null,
  title: '',
  category: 'golden',
  severity: 'S1',
  difficulty: 2,
  expectedType: 'assert',
  expectedText: '',
  inputText: '{}',
  assertionsText: '{"must_contain": ["示例"]}',
  rubricText: '[]',
  tags: '',
  source: 'manual',
  sourceRef: '',
  status: 'active',
})

async function loadCases(): Promise<void> {
  if (!currentSuiteId.value) {
    caseRows.value = []
    caseTotal.value = 0
    return
  }
  caseLoading.value = true
  try {
    const page = await evalApi.cases(currentSuiteId.value, {
      keyword: caseFilters.keyword,
      category: caseFilters.category,
      severity: caseFilters.severity,
      status: caseFilters.status,
      pageNum: casePage.value,
      pageSize: casePageSize.value,
    })
    caseRows.value = page.list ?? []
    caseTotal.value = page.total ?? 0
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载用例失败')
  } finally {
    caseLoading.value = false
  }
}

function reloadCases(): void {
  casePage.value = 1
  void loadCases()
}

const scheduleCaseQuery = useAutoQuery(reloadCases)

function openCaseCreate(): void {
  caseForm.id = null
  caseForm.title = ''
  caseForm.category = 'golden'
  caseForm.severity = 'S1'
  caseForm.difficulty = 2
  caseForm.expectedType = 'assert'
  caseForm.expectedText = ''
  caseForm.inputText = '{}'
  caseForm.assertionsText = '{"must_contain": ["示例"]}'
  caseForm.rubricText = '[]'
  caseForm.tags = ''
  caseForm.source = 'manual'
  caseForm.sourceRef = ''
  caseForm.status = 'active'
  caseDialog.value = true
}

function openCaseEdit(row: EvalCaseDetail): void {
  caseForm.id = row.id
  caseForm.title = row.title
  caseForm.category = row.category
  caseForm.severity = row.severity
  caseForm.difficulty = row.difficulty || 2
  caseForm.expectedType = row.expectedType || 'assert'
  caseForm.expectedText = row.expectedText || ''
  caseForm.inputText = JSON.stringify(row.input ?? {}, null, 2)
  caseForm.assertionsText = JSON.stringify(row.assertions ?? {}, null, 2)
  caseForm.rubricText = JSON.stringify(row.rubric ?? [], null, 2)
  caseForm.tags = row.tags || ''
  caseForm.source = row.source || 'manual'
  caseForm.sourceRef = row.sourceRef || ''
  caseForm.status = row.status
  caseDialog.value = true
}

function tryParse(text: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(text || 'null') }
  } catch {
    return { ok: false }
  }
}

async function saveCase(): Promise<void> {
  if (!currentSuiteId.value) {
    return
  }
  if (!caseForm.title.trim()) {
    ElMessage.warning('标题为必填')
    return
  }
  const input = tryParse(caseForm.inputText)
  const assertions = tryParse(caseForm.assertionsText)
  const rubric = tryParse(caseForm.rubricText)
  if (!input.ok || !assertions.ok || !rubric.ok) {
    ElMessage.warning('输入 / 断言 / Rubric 里有不是合法 JSON 的内容')
    return
  }
  if (typeof assertions.value !== 'object' || assertions.value === null || Array.isArray(assertions.value)) {
    ElMessage.warning('断言必须是 JSON 对象')
    return
  }
  if (!Array.isArray(rubric.value)) {
    ElMessage.warning('Rubric 必须是 JSON 数组')
    return
  }
  caseSaving.value = true
  try {
    const body: Record<string, unknown> = {
      projectId: currentSuite.value?.projectId,
      title: caseForm.title,
      category: caseForm.category,
      severity: caseForm.severity,
      difficulty: Number(caseForm.difficulty),
      expected_type: caseForm.expectedType,
      expected_text: caseForm.expectedText,
      input_json: input.value ?? {},
      assertions: assertions.value,
      rubric_json: rubric.value,
      tags: caseForm.tags,
      source: caseForm.source,
      source_ref: caseForm.sourceRef,
    }
    if (caseForm.id) {
      body.status = caseForm.status
      await evalApi.updateCase(caseForm.id, body)
    } else {
      await evalApi.createCase(currentSuiteId.value, body)
    }
    ElMessage.success('已保存')
    caseDialog.value = false
    await loadCases()
    await loadSuites()
    await loadSuiteOptions()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    caseSaving.value = false
  }
}

async function deleteCase(row: EvalCaseDetail): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除用例「${row.caseNo} ${row.title}」？已进入跑批的用例会被冻结拒绝。`, '删除确认', {
      type: 'warning',
    })
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    return
  }
  try {
    await evalApi.removeCase(row.id)
    ElMessage.success('已删除')
    await loadCases()
    await loadSuites()
    await loadSuiteOptions()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除失败')
  }
}

const importDialog = ref(false)
const importText = ref('')
const importResult = ref<EvalImportResult | null>(null)
const importing = ref(false)

function openImport(): void {
  importText.value = ''
  importResult.value = null
  importDialog.value = true
}

function parseImportCases(): Array<Record<string, unknown>> | null {
  const parsed = tryParse(importText.value)
  if (!parsed.ok || !Array.isArray(parsed.value) || !parsed.value.length) {
    ElMessage.warning('请粘贴非空的 JSON 数组')
    return null
  }
  return parsed.value as Array<Record<string, unknown>>
}

async function dryRunImport(): Promise<void> {
  if (!currentSuiteId.value) return
  const cases = parseImportCases()
  if (!cases) return
  importing.value = true
  try {
    importResult.value = await evalApi.importCases({
      suiteId: currentSuiteId.value,
      projectId: currentSuite.value?.projectId,
      cases,
      dryRun: true,
    })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '干跑失败')
  } finally {
    importing.value = false
  }
}

async function submitImport(): Promise<void> {
  if (!currentSuiteId.value) return
  const cases = parseImportCases()
  if (!cases) return
  importing.value = true
  try {
    importResult.value = await evalApi.importCases({
      suiteId: currentSuiteId.value,
      projectId: currentSuite.value?.projectId,
      cases,
      dryRun: false,
    })
    ElMessage.success(`已导入 ${importResult.value.accepted} 条，跳过 ${importResult.value.skipped} 条`)
    await loadCases()
    await loadSuites()
    await loadSuiteOptions()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '导入失败')
  } finally {
    importing.value = false
  }
}

// ---------------------------------------------------------------- 跑批

const runRows = ref<EvalRun[]>([])
const suiteRunOptions = ref<EvalRun[]>([])
const runTotal = ref(0)
const runPage = ref(1)
const runPageSize = ref(20)
const runLoading = ref(false)
const runFilters = reactive({ status: '', gateConclusion: '' })
const runDialog = ref(false)
const runCreating = ref(false)
const runForm = reactive({
  configId: null as number | null,
  judgeId: null as number | null,
  purpose: 'run',
  ablationGroup: '',
  triggerType: 'manual',
  temperature: 0,
  seed: null as number | null,
})

async function loadRuns(): Promise<void> {
  if (!currentSuiteId.value) {
    runRows.value = []
    runTotal.value = 0
    return
  }
  runLoading.value = true
  try {
    const page = await evalApi.runs({
      projectId: appStore.currentProjectId ?? undefined,
      suiteId: currentSuiteId.value,
      status: runFilters.status,
      gateConclusion: runFilters.gateConclusion,
      pageNum: runPage.value,
      pageSize: runPageSize.value,
    })
    runRows.value = page.list ?? []
    runTotal.value = page.total ?? 0
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载跑批失败')
  } finally {
    runLoading.value = false
  }
}

function reloadRuns(): void {
  runPage.value = 1
  void loadRuns()
}

async function ensureSuiteRunOptions(): Promise<void> {
  if (!currentSuiteId.value) {
    suiteRunOptions.value = []
    return
  }
  try {
    const page = await evalApi.runs({
      projectId: appStore.currentProjectId ?? undefined,
      suiteId: currentSuiteId.value,
      status: 'finished',
      pageNum: 1,
      pageSize: 100,
    })
    suiteRunOptions.value = page.list ?? []
  } catch {
    suiteRunOptions.value = []
  }
}

function openCreateRun(): void {
  if (!currentSuiteId.value) return
  runForm.configId = null
  runForm.judgeId = null
  runForm.purpose = 'run'
  runForm.ablationGroup = ''
  runForm.triggerType = 'manual'
  runForm.temperature = 0
  runForm.seed = null
  runDialog.value = true
  if (!configRows.value.length) void loadConfigs()
  if (!judgeRows.value.length) void loadJudges()
}

async function submitRun(): Promise<void> {
  if (!currentSuiteId.value) return
  runCreating.value = true
  try {
    const config: Record<string, unknown> = runForm.configId
      ? {}
      : { temperature: runForm.temperature, ...(runForm.seed === null ? {} : { seed: runForm.seed }) }
    const result = await evalApi.createRun({
      projectId: appStore.currentProjectId,
      suiteId: currentSuiteId.value,
      configId: runForm.configId,
      judgeId: runForm.judgeId,
      config,
      judge: {},
      purpose: runForm.purpose,
      ablationGroup: runForm.ablationGroup,
      triggerType: runForm.triggerType,
    })
    ElMessage.success(result.reused ? `命中幂等键，复用跑批 #${result.runId}` : `已创建跑批 #${result.runId}`)
    runDialog.value = false
    await loadRuns()
    openReplayFor(result.runId)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建跑批失败')
  } finally {
    runCreating.value = false
  }
}

const replayDialog = ref(false)
const replaying = ref(false)
const replayForm = reactive({ runId: 0, text: '' })

function openReplayFor(runId: number): void {
  replayForm.runId = runId
  replayForm.text = JSON.stringify(
    [{ caseNo: 'EC-001', output: '把该模型对这一条的真实回答粘在这里', latencyMs: 0 }],
    null,
    2,
  )
  replayDialog.value = true
}

async function submitReplay(): Promise<void> {
  const parsed = tryParse(replayForm.text)
  if (!parsed.ok || !Array.isArray(parsed.value)) {
    ElMessage.warning('输出必须是 JSON 数组，每项带 caseNo 或 caseId')
    return
  }
  replaying.value = true
  try {
    await evalApi.resumeRun(replayForm.runId, parsed.value as EvalReplayOutput[])
    ElMessage.success('判定完成')
    replayDialog.value = false
    await loadRuns()
    await loadQuality()
    if (runDetailVisible.value && runDetail.value?.run.id === replayForm.runId) {
      await loadRunDetail(replayForm.runId)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '执行失败')
  } finally {
    replaying.value = false
  }
}

async function resumeRunRow(row: EvalRun): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `续跑只补没有结果的用例；没给模型输出的用例会记为跳过。继续跑批 #${row.id}？`,
      '续跑确认',
      { type: 'warning' },
    )
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    return
  }
  try {
    await evalApi.resumeRun(row.id)
    ElMessage.success('已续跑')
    await loadRuns()
    if (runDetailVisible.value && runDetail.value?.run.id === row.id) {
      await loadRunDetail(row.id)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '续跑失败')
  }
}

async function cancelRunRow(row: EvalRun): Promise<void> {
  try {
    await ElMessageBox.confirm(`取消跑批 #${row.id}？已写入的结果会保留。`, '取消确认', { type: 'warning' })
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    return
  }
  try {
    const result = await evalApi.cancelRun(row.id)
    ElMessage.success(result.idempotent ? '已处于终态，无需取消' : '已取消')
    await loadRuns()
    if (runDetailVisible.value && runDetail.value?.run.id === row.id) {
      await loadRunDetail(row.id)
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '取消失败')
  }
}

function onExportCommand(row: EvalRun, format: string): void {
  if (format !== 'md' && format !== 'xlsx') return
  void exportRun(row, format)
}

function exportDetailRun(format: string): void {
  if (runDetail.value) onExportCommand(runDetail.value.run, format)
}

async function exportRun(row: EvalRun, format: 'md' | 'xlsx'): Promise<void> {
  try {
    const result = await evalApi.exportRun(row.id, format)
    const url = URL.createObjectURL(result.blob)
    const link = document.createElement('a')
    link.href = url
    link.download = result.filename || `eval-run-${row.id}.${format}`
    link.click()
    URL.revokeObjectURL(url)
    ElMessage.success(`已导出 ${result.count ?? row.total} 条结果`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '导出失败')
  }
}

const runDetailVisible = ref(false)
const runDetailLoading = ref(false)
const runDetail = ref<EvalRunDetail | null>(null)
const humanDrafts = ref<Record<number, string>>({})

const runMetricRows = computed(() => {
  const detail = runDetail.value
  if (!detail) return []
  return EVAL_METRICS.map((def) => {
    const row = detail.metrics[def.key]
    if (!row) return null
    return {
      key: def.key,
      label: def.label,
      primary: def.primary,
      description: def.description,
      value: metricText(def.unit, row.value),
      ci: row.ci_lower !== null && row.ci_upper !== null
        ? `${metricText(def.unit, row.ci_lower)} ~ ${metricText(def.unit, row.ci_upper)}`
        : '-',
      sample: row.sample_size,
    }
  }).filter((item): item is NonNullable<typeof item> => item !== null)
})

function metricText(unit: string, value: number | null): string {
  if (value === null || value === undefined) return '-'
  if (unit === 'ratio') return pct(value)
  if (unit === 'ms') return `${value.toFixed(0)} ms`
  if (unit === 'micros') return `${(value / 1_000_000).toFixed(4)} 元`
  if (unit === 'step') return value.toFixed(2)
  return value.toFixed(2)
}

/** 按指标 key 取单位再格式化：门禁/对比/趋势里的值都必须走这里。 */
function metricTextByKey(metricKey: string, value: number | null | undefined): string {
  if (value === null || value === undefined) return '-'
  return metricText(EVAL_METRIC_UNITS[metricKey] || '', value)
}

/** 判定值按哪条线比：min 取 Wilson 下界、max 取上界，无区间时就是点估计。 */
function judgeBasisText(basis: string): string {
  if (basis === 'ci_lower') return 'Wilson 95% 下界'
  if (basis === 'ci_upper') return 'Wilson 95% 上界'
  return '点估计（该指标无置信区间）'
}

async function loadRunDetail(runId: number): Promise<void> {
  runDetailLoading.value = true
  try {
    const detail = await evalApi.run(runId)
    runDetail.value = detail
    const drafts: Record<number, string> = {}
    detail.results.forEach((row: EvalResultRow) => {
      drafts[row.id] = row.human_verdict ?? ''
    })
    humanDrafts.value = drafts
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载跑批详情失败')
  } finally {
    runDetailLoading.value = false
  }
}

async function openRunDetail(row: EvalRun): Promise<void> {
  runDetailVisible.value = true
  runDetail.value = null
  await loadRunDetail(row.id)
}

const hasHumanDraft = computed(() =>
  Object.values(humanDrafts.value).some((value) => value === 'pass' || value === 'fail'),
)

async function saveHumanScores(): Promise<void> {
  if (!runDetail.value) return
  const items = Object.entries(humanDrafts.value)
    .filter(([, value]) => value === 'pass' || value === 'fail')
    .map(([id, value]) => ({ resultId: Number(id), human: value }))
  if (!items.length) {
    ElMessage.warning('还没有填写任何一条人评')
    return
  }
  try {
    const result = await evalApi.humanScores(runDetail.value.run.id, items, '本地用户')
    ElMessage.success(`已回填 ${result.updated} 条人评，指标已重算`)
    await loadRunDetail(runDetail.value.run.id)
    await loadRuns()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '回填失败')
  }
}

const compareDialog = ref(false)
const compareLoading = ref(false)
const compareResult = ref<EvalCompareResult | null>(null)
const compareForm = reactive({ baselineRunId: null as number | null, currentRunId: null as number | null, alpha: 0.05 })

async function openCompare(row: EvalRun): Promise<void> {
  compareForm.currentRunId = row.id
  compareForm.baselineRunId = null
  compareForm.alpha = 0.05
  compareResult.value = null
  compareDialog.value = true
  await ensureSuiteRunOptions()
  compareForm.baselineRunId = suiteRunOptions.value.find((item) => item.id !== row.id)?.id ?? null
}

async function submitCompare(): Promise<void> {
  if (!compareForm.baselineRunId || !compareForm.currentRunId) {
    ElMessage.warning('请选择基线跑批与当前跑批')
    return
  }
  if (compareForm.baselineRunId === compareForm.currentRunId) {
    ElMessage.warning('基线跑批与当前跑批不能相同')
    return
  }
  compareLoading.value = true
  try {
    compareResult.value = await evalApi.compare(
      compareForm.baselineRunId,
      compareForm.currentRunId,
      compareForm.alpha,
    )
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '对比失败')
  } finally {
    compareLoading.value = false
  }
}

function deltaText(row: { key: string; delta: number | null }): string {
  if (row.delta === null) return '-'
  const sign = row.delta > 0 ? '+' : ''
  const unit = EVAL_METRIC_UNITS[row.key] || ''
  if (unit === 'ratio') return `${sign}${(row.delta * 100).toFixed(2)}pp`
  if (unit === 'ms') return `${sign}${row.delta.toFixed(0)} ms`
  if (unit === 'micros') return `${sign}${(row.delta / 1_000_000).toFixed(4)} 元`
  if (unit === 'step') return `${sign}${row.delta.toFixed(2)} 步`
  return `${sign}${row.delta.toFixed(2)}`
}

function deltaClass(delta: number | null): string {
  if (delta === null) return 'muted'
  return delta >= 0 ? 'delta-good' : 'delta-bad'
}

// ---------------------------------------------------------------- 质量

const qualityLoading = ref(false)
const quality = ref<EvalSuiteQuality | null>(null)
const qualityTrends = ref<EvalTrendPoint[]>([])

async function loadQuality(): Promise<void> {
  if (!currentSuiteId.value) return
  qualityLoading.value = true
  try {
    quality.value = await evalApi.quality(currentSuiteId.value)
    const trends = await evalApi.trends(currentSuiteId.value, ['severity_weighted_error', 'hallucination_rate'], 10)
    qualityTrends.value = trends.points
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载质量数据失败')
  } finally {
    qualityLoading.value = false
  }
}

function trendValue(point: EvalTrendPoint, key: string): string {
  const row = point.metrics[key]
  return row ? metricTextByKey(key, row.value) : '-'
}

// ---------------------------------------------------------------- 门禁

interface GateRuleForm {
  thresholdKey: string
  label: string
  direction: 'min' | 'max'
  defaultLimit: number
  enabled: boolean
  limit: number
  primary: boolean
  kappaMin?: number
  requireProviderCost?: boolean
}

const gateRows = ref<EvalGate[]>([])
const gateLoading = ref(false)
const gateDialog = ref(false)
const gateSaving = ref(false)
const gateForm = reactive({
  id: null as number | null,
  name: '',
  minExplore: 30,
  minPublish: 200,
  isHard: true,
  enabled: true,
  baselineRunId: null as number | null,
  rules: [] as GateRuleForm[],
})

function initGateRules(): void {
  gateForm.rules = EVAL_GATE_RULES.map((rule) => ({
    thresholdKey: rule.thresholdKey,
    label: rule.label,
    direction: rule.direction,
    defaultLimit: rule.defaultLimit,
    enabled: rule.defaultEnabled,
    limit: rule.defaultLimit,
    primary: rule.primary,
    kappaMin: rule.kappaMin,
    requireProviderCost: rule.requireProviderCost,
  }))
}

async function loadGates(): Promise<void> {
  if (!currentSuiteId.value) {
    gateRows.value = []
    return
  }
  gateLoading.value = true
  try {
    const result = await evalApi.gates({
      projectId: appStore.currentProjectId ?? undefined,
      suiteId: currentSuiteId.value,
    })
    gateRows.value = result.list ?? []
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加载门禁失败')
  } finally {
    gateLoading.value = false
  }
}

function openGateCreate(): void {
  gateForm.id = null
  gateForm.name = `${currentSuite.value?.name ?? '评测集'}-门禁`
  gateForm.minExplore = 30
  gateForm.minPublish = 200
  gateForm.isHard = true
  gateForm.enabled = true
  gateForm.baselineRunId = null
  initGateRules()
  gateDialog.value = true
  void ensureSuiteRunOptions()
}

function openGateEdit(row: EvalGate): void {
  gateForm.id = row.id
  gateForm.name = row.name
  gateForm.isHard = row.isHard
  gateForm.enabled = row.enabled
  gateForm.baselineRunId = row.baselineRunId
  initGateRules()
  const thresholds = row.thresholds || {}
  gateForm.minExplore = typeof thresholds.minExplore === 'number' ? thresholds.minExplore : 30
  gateForm.minPublish = typeof thresholds.minPublish === 'number' ? thresholds.minPublish : 200
  gateForm.rules.forEach((rule) => {
    const raw = thresholds[rule.thresholdKey]
    if (raw && typeof raw === 'object') {
      rule.enabled = true
      const record = raw as Record<string, unknown>
      if (typeof record.min === 'number') rule.limit = record.min
      if (typeof record.max === 'number') rule.limit = record.max
    } else {
      rule.enabled = false
    }
  })
  gateDialog.value = true
  void ensureSuiteRunOptions()
}

function gateThresholdSummary(row: EvalGate): string {
  const thresholds = row.thresholds || {}
  const keys = EVAL_GATE_RULES.filter((rule) => Boolean(thresholds[rule.thresholdKey])).map((rule) => rule.label)
  const minExplore = thresholds.minExplore ?? 30
  const minPublish = thresholds.minPublish ?? 200
  return `${keys.length} 项规则（${keys.slice(0, 3).join('、')}${keys.length > 3 ? '…' : ''}）；样本 ${minExplore}/${minPublish}`
}

async function saveGate(): Promise<void> {
  if (!currentSuiteId.value) return
  if (!gateForm.name.trim()) {
    ElMessage.warning('门禁名称不能为空')
    return
  }
  const thresholds: Record<string, unknown> = {
    minExplore: Number(gateForm.minExplore),
    minPublish: Number(gateForm.minPublish),
  }
  gateForm.rules.forEach((rule) => {
    if (!rule.enabled) return
    const value: Record<string, unknown> =
      rule.direction === 'min' ? { min: Number(rule.limit) } : { max: Number(rule.limit) }
    if (rule.kappaMin !== undefined) value.kappaMin = rule.kappaMin
    if (rule.requireProviderCost) value.requireProviderCost = true
    thresholds[rule.thresholdKey] = value
  })
  gateSaving.value = true
  try {
    if (gateForm.id) {
      await evalApi.updateGate(gateForm.id, {
        name: gateForm.name,
        thresholds,
        isHard: gateForm.isHard,
        enabled: gateForm.enabled,
        baselineRunId: gateForm.baselineRunId,
      })
    } else {
      await evalApi.createGate({
        projectId: appStore.currentProjectId,
        suiteId: currentSuiteId.value,
        name: gateForm.name,
        thresholds,
        isHard: gateForm.isHard,
        baselineRunId: gateForm.baselineRunId,
      })
    }
    ElMessage.success('已保存')
    gateDialog.value = false
    await loadGates()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存失败')
  } finally {
    gateSaving.value = false
  }
}

async function toggleGate(row: EvalGate): Promise<void> {
  const next = row.enabled
  try {
    await evalApi.updateGate(row.id, { enabled: next })
    ElMessage.success(next ? '已启用' : '已停用')
  } catch (err) {
    row.enabled = !next
    ElMessage.error(err instanceof Error ? err.message : '更新失败')
  }
}

const gateEvalDialog = ref(false)
const gateEvaluating = ref(false)
const gateEvalTarget = ref<EvalGate | null>(null)
const gateEvalRunId = ref<number | null>(null)
const gateResultDialog = ref(false)
const gateResult = ref<EvalGateEvaluation | null>(null)

async function openGateEvaluate(row: EvalGate): Promise<void> {
  gateEvalTarget.value = row
  gateEvalRunId.value = row.lastRunId
  gateResult.value = null
  gateEvalDialog.value = true
  await ensureSuiteRunOptions()
  gateEvalRunId.value = gateEvalRunId.value ?? suiteRunOptions.value[0]?.id ?? null
}

async function submitGateEvaluation(): Promise<void> {
  if (!gateEvalTarget.value || !gateEvalRunId.value) return
  gateEvaluating.value = true
  try {
    gateResult.value = await evalApi.evaluateGate(gateEvalTarget.value.id, gateEvalRunId.value)
    gateEvalDialog.value = false
    gateResultDialog.value = true
    await loadGates()
    await loadRuns()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '求值失败')
  } finally {
    gateEvaluating.value = false
  }
}

function gateRuleText(check: { metric: string; limit: number | Record<string, unknown> }): string {
  const unit = EVAL_METRIC_UNITS[check.metric] || ''
  const fmt = (value: number): string => {
    if (unit === 'ratio') return pct(value)
    if (unit === 'ms') return `${value} ms`
    if (unit === 'micros') return `${(value / 1_000_000).toFixed(4)} 元`
    if (unit === 'step') return `${value} 步`
    return String(value)
  }
  if (typeof check.limit === 'number') return fmt(check.limit)
  const rule = check.limit
  const parts: string[] = []
  if (typeof rule.min === 'number') parts.push(`≥ ${fmt(rule.min)}`)
  if (typeof rule.max === 'number') parts.push(`≤ ${fmt(rule.max)}`)
  if (typeof rule.kappaMin === 'number') parts.push(`κ ≥ ${rule.kappaMin}`)
  return parts.join(' / ') || '-'
}

// ---------------------------------------------------------------- 配置 / 判定器（编辑入口在 设置 → AI 设置 → AI 评测配置）
const configRows = ref<EvalConfig[]>([])
const judgeRows = ref<EvalJudge[]>([])

async function loadConfigs(): Promise<void> {
  try {
    const page = await evalApi.configs({
      projectId: appStore.currentProjectId ?? undefined,
      pageNum: 1,
      pageSize: 100,
    })
    configRows.value = page.list ?? []
  } catch (err) {
    configRows.value = []
    ElMessage.error(err instanceof Error ? err.message : '加载配置失败')
  }
}

async function loadJudges(): Promise<void> {
  try {
    const page = await evalApi.judges({
      projectId: appStore.currentProjectId ?? undefined,
      pageNum: 1,
      pageSize: 100,
    })
    judgeRows.value = page.list ?? []
  } catch (err) {
    judgeRows.value = []
    ElMessage.error(err instanceof Error ? err.message : '加载判定器失败')
  }
}

// ---------------------------------------------------------------- 生命周期

useProjectScope({
  load: () => {
    void loadSuiteOptions()
    void loadConfigs()
    void loadJudges()
  },
  resetScopedFilters: () => {
    currentSuiteId.value = null
    activeTab.value = 'suites'
    suiteFilters.keyword = ''
    suiteFilters.targetType = ''
    suiteFilters.visibility = ''
    suiteFilters.status = ''
    caseFilters.keyword = ''
    caseFilters.category = ''
    caseFilters.severity = ''
    caseFilters.status = ''
    runFilters.status = ''
    runFilters.gateConclusion = ''
    suiteRunOptions.value = []
    quality.value = null
    qualityTrends.value = []
    gateRows.value = []
    reloadSuites()
  },
})

onMounted(async () => {
  await appStore.loadProjects()
  initGateRules()
  await loadSuites()
})
</script>

<style scoped>
.eval-context {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.eval-settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 1280px) {
  .eval-settings-grid {
    grid-template-columns: 1fr;
  }
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.ellipsis {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.form-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 12px;
}

.config-alert {
  margin-bottom: 14px;
}

.config-section-title {
  margin: 2px 0 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.config-section-title:not(:first-of-type) {
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.config-row {
  display: flex;
  gap: 16px;
}

.config-row > .el-form-item {
  flex: 1 1 0;
  min-width: 0;
}

.field-block {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sampling-row {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
}

.sampling-slider {
  flex: 1;
  min-width: 0;
}

.field-hint {
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.form-hint {
  font-size: 12px;
  line-height: 1.6;
}

.json-area :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.quality-title {
  margin: 18px 0 10px;
}

.quality-table {
  margin-top: 10px;
}

.quality-hint {
  margin-top: 8px;
}

.primary-tag {
  margin-left: 6px;
}

.result-expand {
  padding: 4px 12px 8px;
}

.result-expand pre {
  margin: 4px 0 10px;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  background: var(--el-fill-color-light);
  padding: 8px;
  border-radius: 4px;
}

.compare-table {
  margin-top: 12px;
}

.delta-good {
  color: var(--el-color-success);
}

.delta-bad {
  color: var(--el-color-danger);
}

.gate-rules {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}

.gate-rule {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.gate-notes {
  margin: 10px 0;
  padding-left: 20px;
  color: var(--el-text-color-regular);
  line-height: 1.8;
}

.import-result {
  margin-top: 12px;
}
</style>