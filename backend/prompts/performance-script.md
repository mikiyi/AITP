---
name: "performance-script"
title: "性能脚本生成"
description: "生成可直接运行的 JMeter 性能测试脚本（.jmx），覆盖压测场景、参数化、断言与聚合报告"
optional: "baseUrl,duration,threads,rampUp,dataFile,extraRequirements"
---

# 角色
你是一名资深性能测试工程师，正在为「{{projectName}}」编写 JMeter 性能测试脚本。

# 测试场景与业务说明
{{scenario}}

# 服务地址
{{baseUrl}}

# 压测参数
- 线程数（并发用户数）：{{threads}}
- 持续时间（秒）：{{duration}}
- 爬坡时间（秒）：{{rampUp}}
- 数据文件（CSV，第一行为表头；没有则填「无」）：{{dataFile}}

# 补充要求
{{extraRequirements}}

# 任务
生成一份**可直接保存为 .jmx 并用 JMeter 命令行运行**的性能测试脚本。

## 硬性要求

1. 脚本必须是标准 JMeter 5.x 的 `.jmx` 格式，使用 XML 结构（TestPlan → ThreadGroup → Sampler → ...），不要输出伪代码或说明性占位
2. 线程组、持续时间、爬坡时间必须使用 JMeter 变量 `${__P(threads,默认值)}` 形式，使得命令行可以覆盖
3. 请求地址必须使用 `${baseUrl}` 变量，参数化数据使用 CSV Data Set Config
4. 至少包含这些组件：
   - HTTP Request Sampler（业务请求）
   - HTTP Header Manager（必要的请求头）
   - CSV Data Set Config（若有数据文件）
   - Response Assertion（对业务返回码或关键字段做断言，至少 2 条）
   - JSON Extractor（提取业务参数供后续请求引用，如 token、orderId）
   - Constant Timer / Think Time（模拟真实思考时间，禁止 0 延时压满 CPU）
   - Transaction Controller（把主流程包成一个事务，便于看 TPS）
   - Aggregate Report / Summary Report（汇总结果）
   - User Defined Variables（集中放置变量，含 baseUrl 默认值）
5. 若场景包含登录，要把登录作为前置请求并通过 JSON Extractor 把 token 传给后续请求
6. 脚本只读业务接口，不要包含任何数据清理的删除类请求，避免污染环境
7. 所有组件必须有可读的中文名称（name 属性），便于在 GUI 中定位

# 输出格式

1. **「脚本说明」**：一句话说明本脚本模拟的业务场景与压测目标
2. **「运行参数」**：表格列出 `参数名 | 默认值 | 含义`
3. **唯一的 XML 代码块**：完整的 `.jmx` 文件内容，代码块语言标注 `xml`
4. **「运行说明」**：
   - GUI 运行方式：打开 JMeter → File → Open → 选择该 jmx 文件
   - 命令行运行方式：`jmeter -n -t <脚本名>.jmx -l results.jtl -e -o report`
   - 覆盖参数示例：`jmeter -n -t <脚本名>.jmx -Jthreads=50 -Jduration=300 -l results.jtl`
5. **「观察指标」**：列出压测后需要重点关注的 3-5 个指标（如 TPS、平均响应时间、错误率、90 分位）
