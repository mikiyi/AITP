<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">操作日志</h2>
    </div>

    <div class="panel panel-pad settings-block">
      <div class="settings-block__head">
        <h3>关键操作日志</h3>
        <span class="muted">关键写操作都会留痕，可按动作与资源类型筛选</span>
      </div>
      <BaseProTable
        :columns="auditColumns"
        :fetcher="auditFetcher"
        :search-fields="auditSearchFields"
        :max-height="560"
        sync-query
        query-prefix="audit_"
      >
        <template #action="{ row }">{{ actionLabel(row.action) }}</template>
        <template #resourceType="{ row }">{{ resourceLabel(row.resourceType) }}</template>
      </BaseProTable>
    </div>
  </div>
</template>

<script setup lang="ts">
import BaseProTable, { type ColumnDef, type SearchField } from '@/components/BaseProTable.vue'
import { settingsApi } from '@/api'

/** 审计动作中文名（与后端 audit_service 的 AUDIT_ACTION_* 对应）。 */
const AUDIT_ACTION_OPTIONS = [
  { label: '新增', value: 'create' },
  { label: '修改', value: 'update' },
  { label: '删除', value: 'delete' },
  { label: '审核', value: 'review' },
  { label: '撤销', value: 'revoke' },
  { label: '状态流转', value: 'transition' },
  { label: '导入', value: 'import' },
  { label: '导出', value: 'export' },
  { label: '生成', value: 'generate' },
  { label: '配置', value: 'config' },
  { label: '备份', value: 'backup' },
  { label: '还原', value: 'restore' },
  { label: '执行', value: 'execute' },
]

/** 资源类型中文名（与后端 audit_service 的 RESOURCE_* 对应）。 */
const AUDIT_RESOURCE_OPTIONS = [
  { label: 'AI 档案', value: 'ai_profile' },
  { label: 'AI 提示词', value: 'ai_prompt' },
  { label: 'AI 任务', value: 'ai_task' },
  { label: 'AI 执行记录', value: 'ai_record' },
  { label: '模块', value: 'module' },
  { label: '模块能力绑定', value: 'module_capability' },
  { label: '知识条目', value: 'knowledge_article' },
  { label: '知识待审条目', value: 'knowledge_draft' },
  { label: '知识版本', value: 'knowledge_version' },
  { label: '知识分类', value: 'knowledge_category' },
  { label: '测试用例', value: 'test_case' },
  { label: '缺陷', value: 'bug' },
  { label: '测试报告', value: 'report' },
  { label: '项目', value: 'project' },
  { label: '需求', value: 'requirement' },
  { label: '系统配置', value: 'system_config' },
  { label: '数据库备份', value: 'db_backup' },
  { label: '编排入口', value: 'workbench_entry' },
  { label: '验证案例', value: 'validation_case' },
  { label: '实验室附件', value: 'lab_attachment' },
  { label: '回归清单', value: 'regression' },
  { label: '实体关联', value: 'entity_relation' },
  { label: '任务清单', value: 'todo' },
  { label: '自动化脚本', value: 'automation_script' },
  { label: '自动化执行', value: 'automation_run' },
  { label: '测试文档模板', value: 'doc_template' },
  { label: 'SQL 分析', value: 'sql_analysis' },
  { label: '测试数据', value: 'test_data' },
  { label: '测试环境', value: 'test_env' },
]

const auditColumns: ColumnDef[] = [
  { prop: 'createdAt', label: '时间', width: 170 },
  { prop: 'actor', label: '操作者', width: 110 },
  { prop: 'action', label: '动作', width: 120, slot: 'action' },
  { prop: 'resourceType', label: '资源类型', width: 140, slot: 'resourceType' },
  { prop: 'resourceLabel', label: '资源', minWidth: 200 },
  { prop: 'result', label: '结果', width: 90 },
  { prop: 'traceId', label: 'TraceId', width: 200 },
]

const auditSearchFields: SearchField[] = [
  { prop: 'action', label: '动作', type: 'select', options: AUDIT_ACTION_OPTIONS },
  { prop: 'resourceType', label: '资源类型', type: 'select', options: AUDIT_RESOURCE_OPTIONS },
]

function actionLabel(value: string): string {
  return AUDIT_ACTION_OPTIONS.find((item) => item.value === value)?.label ?? value
}

function resourceLabel(value: string): string {
  return AUDIT_RESOURCE_OPTIONS.find((item) => item.value === value)?.label ?? value
}

function auditFetcher(query: Record<string, unknown>) {
  return settingsApi.auditLogs(query)
}
</script>

<style scoped>
.settings-block {
  margin-bottom: 16px;
}

.settings-block__head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.settings-block__head h3 {
  margin: 0;
  font-size: 15px;
}
</style>
