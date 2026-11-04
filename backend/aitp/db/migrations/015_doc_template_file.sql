-- =====================================================================
-- AITP 迁移 015：模板上传文件与版式说明
--   上传 docx / xlsx / md 等模板文件后，除了正文，还要记录：
--   - file_name / file_type / file_size：让用户知道模板来自哪份文件；
--   - style_hint：从文件里解析出的字体、字号、工作表等版式要求，
--     和正文一起渲染进提示词，AI 才能按同样的文档风格输出。
-- =====================================================================

ALTER TABLE sys_doc_template ADD COLUMN style_hint TEXT NOT NULL DEFAULT '';
ALTER TABLE sys_doc_template ADD COLUMN file_name  TEXT NOT NULL DEFAULT '';
ALTER TABLE sys_doc_template ADD COLUMN file_type  TEXT NOT NULL DEFAULT '';
ALTER TABLE sys_doc_template ADD COLUMN file_size  INTEGER NOT NULL DEFAULT 0;
