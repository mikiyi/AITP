-- =====================================================================
-- AITP 迁移 014：测试文档模板
--   「设置 → 测试文档模板」维护产出物模板，按类型区分：
--     case    测试用例（用例生成 / XMind 转用例 / 接口测试设计可用）
--     report  测试报告（报告生成可用）
--     mindmap 测试点脑图（测试点脑图可用）
--   能力只能选同类型模板，避免把报告模板套到用例能力上。
--   content 约束模型产出结构（渲染进提示词的 outputTemplate 变量），
--   columns_json 决定导出 Excel 的列顺序（仅测试用例类型使用）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS sys_doc_template (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'case',
    description  TEXT NOT NULL DEFAULT '',
    columns_json TEXT NOT NULL DEFAULT '[]',
    content      TEXT NOT NULL DEFAULT '',
    is_default   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    is_deleted   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sys_doc_template_kind
    ON sys_doc_template(kind);

INSERT INTO sys_doc_template
    (name, kind, description, columns_json, content, is_default, created_at, updated_at)
SELECT
    '标准测试用例模板',
    'case',
    '字段齐全的用例结构，导出 Excel 时按这套列顺序',
    '["用例编号","用例标题","用例类型","优先级","所属模块","前置条件","测试步骤","预期结果","标签"]',
    '每条用例都要包含下列字段，字段含义与取值要求如下：
- 用例标题：格式「场景-条件-预期」，同类用例不重复
- 用例类型：functional / interface / boundary / security / ui / exception
- 优先级：P0 / P1 / P2 / P3
- 所属模块：功能点或接口名
- 前置条件：没有写「无」
- 测试步骤：逐步写清操作与每步预期，边界值写具体数值
- 预期结果：可判定的整体结果
- 标签：逗号分隔
输出格式仍遵循该能力自身的约定（JSON 能力输出 JSON 数组，Markdown 能力输出表格）。',
    1,
    '2026-11-04T00:00:00Z',
    '2026-11-04T00:00:00Z'
WHERE NOT EXISTS (SELECT 1 FROM sys_doc_template WHERE kind = 'case');

INSERT INTO sys_doc_template
    (name, kind, description, columns_json, content, is_default, created_at, updated_at)
SELECT
    '标准测试报告模板',
    'report',
    '测试总结报告的标准章节顺序',
    '[]',
    '报告按以下章节顺序组织，每章给出小标题与要点，不写空话：
1. 测试概述：版本、范围、起止时间
2. 测试范围与策略：覆盖的功能点、未覆盖部分及原因
3. 执行结果：用例总数、通过/失败/阻塞、通过率（写具体数字）
4. 缺陷分析：按严重级别与模块分布，列出关键缺陷编号
5. 风险与建议：遗留风险、上线建议（不下最终结论，由人工决定）
6. 结论摘要：三到五条要点
全文使用 Markdown，表格用 Markdown 表格。',
    1,
    '2026-11-04T00:00:00Z',
    '2026-11-04T00:00:00Z'
WHERE NOT EXISTS (SELECT 1 FROM sys_doc_template WHERE kind = 'report');

INSERT INTO sys_doc_template
    (name, kind, description, columns_json, content, is_default, created_at, updated_at)
SELECT
    '标准测试点脑图模板',
    'mindmap',
    '三层结构的测试点脑图（场景 → 功能 → 测试点）',
    '[]',
    '输出 JSON 树，节点字段固定为 name 与 children：
- 第一层：测试场景（如 登录、下单）
- 第二层：功能点或测试维度（正常流程、边界值、异常、权限）
- 第三层：可直接执行验证的测试点，写清输入条件与预期
层级不超过三层，叶子节点必须是测试点而不是模块名。',
    1,
    '2026-11-04T00:00:00Z',
    '2026-11-04T00:00:00Z'
WHERE NOT EXISTS (SELECT 1 FROM sys_doc_template WHERE kind = 'mindmap');
