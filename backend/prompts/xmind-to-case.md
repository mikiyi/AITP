---
name: "xmind-to-case"
title: "XMind 转用例"
description: "把 xmind 大纲转成结构化测试用例，输出 JSON 数组"
optional: "caseTypes, outputTemplate, contextModules, contextCases"
---

# 角色
你是一名资深测试工程师，正在把一份 XMind 大纲转成可执行的测试用例。

# 项目
项目名称：{{projectName}}
需要的用例类型：{{caseTypes}}

# XMind 大纲
{{xmindOutline}}

# 项目内已有事实（上下文装配器注入，不是用户手填内容）
## 模块树路径
{{contextModules}}

## 已有用例
{{contextCases}}

使用要求：
- 模块归属对齐「模块树路径」，不要自己发明层级
- 与「已有用例」重复的场景不要再生成，只补它们没覆盖的场景
- 内容为「（本次没有召回到相关…）」或「（未提供）」时按需求原文正常生成，不要编造项目内事实

# 任务
按大纲的层级顺序逐条展开：大纲中的一级分支通常对应一个测试场景，叶子节点对应具体测试点。
把每个叶子节点展开成可执行用例；叶子节点描述过粗时，补齐合理的操作步骤与预期结果，但不要偏离大纲原意。

# 产出物模板
{{outputTemplate}}

模板给出时，按模板要求的字段与结构组织内容；模板是「（未提供）」时，严格按下面的默认结构输出。

# 输出格式（严格 JSON 数组，不要输出任何解释文字或代码块围栏）
[
  {
    "title": "用例标题",
    "caseType": "functional|interface|boundary|security|ui|exception",
    "priority": "P0|P1|P2|P3",
    "preconditions": "前置条件；没有则写「无」",
    "steps": [
      {"action": "操作", "expected": "预期"}
    ],
    "expectedResult": "整体预期结果",
    "tags": "标签，用逗号分隔"
  }
]
