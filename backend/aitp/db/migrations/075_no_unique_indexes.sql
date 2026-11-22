-- =====================================================================
-- AITP 迁移 075：需求 / 场景 / 场景执行编号补唯一索引
--   这三类编号一直靠 next_no 的写事务取号，库层却没有唯一索引兜底，
--   取号与插入之间一旦失去事务（新调用点漏接 begin_claim）就会写出重复号。
--   与缺陷编号不同，这里保持各自原有口径不变：
--     需求按项目内唯一（req_no 列还混有外部平台导入的映射号），
--     场景与场景执行全局唯一（取号本就不分项目）。
--   另把需求的外部导入幂等键补成与用例 / 缺陷一致的 UNIQUE（052 漏写成普通 INDEX）。
--   存量若跨项目/同号，保留 id 最小的一条，其余顺延到同组当前最大号之后；
--   无重复时 _*_dup 为空集，UPDATE 天然不touch任何行，覆盖安装不受影响。
-- =====================================================================

-- ---- 需求：项目内 (project_id, req_no) 唯一，与 biz_test_case 同口径 ----
CREATE TEMP TABLE _req_dup AS
SELECT b.id, b.project_id,
       ROW_NUMBER() OVER (PARTITION BY b.project_id ORDER BY b.id) AS rn
FROM biz_requirement b
WHERE b.is_deleted = 0
  AND EXISTS (
      SELECT 1 FROM biz_requirement x
      WHERE x.is_deleted = 0 AND x.project_id = b.project_id
        AND x.req_no = b.req_no AND x.id < b.id
  );

CREATE TEMP TABLE _req_base AS
SELECT project_id, COALESCE(MAX(CAST(substr(req_no, 5) AS INTEGER)), 0) AS n
FROM biz_requirement
WHERE req_no GLOB 'REQ-[0-9]*' AND is_deleted = 0
GROUP BY project_id;

UPDATE biz_requirement
SET req_no = 'REQ-' || printf('%03d',
        COALESCE((SELECT n FROM _req_base WHERE _req_base.project_id = biz_requirement.project_id), 0)
        + (SELECT rn FROM _req_dup WHERE _req_dup.id = biz_requirement.id))
WHERE id IN (SELECT id FROM _req_dup);

DROP TABLE _req_dup;
DROP TABLE _req_base;

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_requirement_no
    ON biz_requirement(project_id, req_no) WHERE is_deleted = 0;

-- ---- 场景：scenario_no 全局唯一 ----
CREATE TEMP TABLE _scn_dup AS
SELECT b.id, ROW_NUMBER() OVER (ORDER BY b.id) AS rn
FROM biz_api_scenario b
WHERE b.is_deleted = 0
  AND EXISTS (
      SELECT 1 FROM biz_api_scenario x
      WHERE x.is_deleted = 0 AND x.scenario_no = b.scenario_no AND x.id < b.id
  );

CREATE TEMP TABLE _scn_base AS
SELECT COALESCE(MAX(CAST(substr(scenario_no, 5) AS INTEGER)), 0) AS n
FROM biz_api_scenario
WHERE scenario_no GLOB 'SCN-[0-9]*' AND is_deleted = 0;

UPDATE biz_api_scenario
SET scenario_no = 'SCN-' || printf('%03d',
        (SELECT n FROM _scn_base) + (SELECT rn FROM _scn_dup WHERE _scn_dup.id = biz_api_scenario.id))
WHERE id IN (SELECT id FROM _scn_dup);

DROP TABLE _scn_dup;
DROP TABLE _scn_base;

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_api_scenario_no
    ON biz_api_scenario(scenario_no) WHERE is_deleted = 0;

-- ---- 场景执行：run_no 全局唯一（历史号只增不复用，不参与最小空缺） ----
CREATE TEMP TABLE _scnr_dup AS
SELECT b.id, ROW_NUMBER() OVER (ORDER BY b.id) AS rn
FROM biz_api_scenario_run b
WHERE b.is_deleted = 0
  AND EXISTS (
      SELECT 1 FROM biz_api_scenario_run x
      WHERE x.is_deleted = 0 AND x.run_no = b.run_no AND x.id < b.id
  );

CREATE TEMP TABLE _scnr_base AS
SELECT COALESCE(MAX(CAST(substr(run_no, 6) AS INTEGER)), 0) AS n
FROM biz_api_scenario_run
WHERE run_no GLOB 'SCNR-[0-9]*' AND is_deleted = 0;

UPDATE biz_api_scenario_run
SET run_no = 'SCNR-' || printf('%03d',
        (SELECT n FROM _scnr_base) + (SELECT rn FROM _scnr_dup WHERE _scnr_dup.id = biz_api_scenario_run.id))
WHERE id IN (SELECT id FROM _scnr_dup);

DROP TABLE _scnr_dup;
DROP TABLE _scnr_base;

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_api_scenario_run_no
    ON biz_api_scenario_run(run_no) WHERE is_deleted = 0;

-- ---- 外部导入幂等键：与用例 / 缺陷同口径 ----
--   052 建这三张表的外部键索引时，用例与缺陷都是 UNIQUE，需求漏成了普通 INDEX：
--   同样的「同项目 + 同平台 + 同外部 ID 只应有一条」，需求只靠应用层 _exists 拦，
--   并发导入时没有库层兜底。这里补成与 biz_test_case / biz_bug 完全一致的定义，
--   并删掉被它覆盖的旧索引，避免留下两个同列索引。
DROP INDEX IF EXISTS idx_biz_requirement_external;

--   建索引前先给存量让路：并发导入曾可以绕过应用层 _exists 写出同键多行，
--   直接建唯一索引会让这批库的升级整体失败。id 最小的一条继续作为导入映射，
--   其余只解除外部标记（行与内容全部保留、不软删），下次导入该外部 id 时重新入库。
UPDATE biz_requirement
SET external_platform = '', external_id = '', external_url = ''
WHERE is_deleted = 0 AND external_id <> ''
  AND EXISTS (
      SELECT 1 FROM biz_requirement x
      WHERE x.is_deleted = 0 AND x.external_id <> ''
        AND x.project_id = biz_requirement.project_id
        AND x.external_platform = biz_requirement.external_platform
        AND x.external_id = biz_requirement.external_id AND x.id < biz_requirement.id
  );

CREATE UNIQUE INDEX IF NOT EXISTS uk_biz_requirement_external
    ON biz_requirement(project_id, external_platform, external_id)
    WHERE external_id <> '' AND is_deleted = 0;
