<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">帮助文档</h2>
      <el-button :icon="Guide" @click="reopenTour">重新打开首启引导</el-button>
      <el-button :icon="MagicStick" @click="activeTab = 'demo'">直接看 Demo 示例</el-button>
    </div>

    <div class="panel panel-pad">
      <el-tabs v-model="activeTab" type="border-card">
        <!-- ==================== 1. 产品介绍 ==================== -->
        <el-tab-pane label="产品介绍" name="intro">
          <div class="doc-body doc-body-wide">
            <h3 class="doc-h3">它是什么</h3>
            <p class="doc-p">
              <BrandName size="small" />（简称 AITP）是一款<strong>个人版的 AI 测试工作台</strong>：
              具有单用户、本地优先、离线可用、提示词工程管理，大模型调用数据脱敏特点。它把 AI 实验室、测试资产、执行轮次、缺陷、回归、准入、报告、
              知识库、自动化与运维配置收进同一条闭环，利用AI的LLM辅助测试工作，让一个版本从头到尾的测试过程快速高效的在同一台机器上完成、留痕、可复盘。
            </p>
            <div class="chain">
              <template v-for="(node, index) in LOOP_NODES" :key="node">
                <span class="chain__node">{{ node }}</span>
                <span v-if="index < LOOP_NODES.length - 1" class="chain__arrow">→</span>
              </template>
            </div>
            <p class="muted doc-p">中间随时可以把待办推进度挂进「任务清单」，长耗时操作交给「任务队列」。</p>

            <h3 class="doc-h3">产品规模（可在代码里数出来的数字）</h3>
            <table class="doc-table stats-table">
              <tbody>
                <tr v-for="stat in PRODUCT_STATS" :key="stat.label">
                  <td class="stats-label">{{ stat.label }}</td>
                  <td class="stats-value">{{ stat.value }}</td>
                  <td>{{ stat.note }}</td>
                </tr>
              </tbody>
            </table>

            <h3 class="doc-h3">测试流程在哪里断，它就在哪里接上</h3>
            <table class="doc-table">
              <thead>
                <tr>
                  <th class="col-stage">环节</th>
                  <th>传统做法断在哪</th>
                  <th>断了之后实际会痛在哪</th>
                  <th>本产品的落点</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="point in PAIN_POINTS" :key="point.stage">
                  <td class="col-stage">{{ point.stage }}</td>
                  <td>{{ point.pain }}</td>
                  <td>{{ point.cost }}</td>
                  <td>
                    {{ point.aitp }}
                    <div class="cell-where">{{ point.where }}</div>
                  </td>
                </tr>
              </tbody>
            </table>

            <h3 class="doc-h3">谁适合用，谁不该用它</h3>
            <div class="aud-grid">
              <div class="panel aud-panel">
                <h4 class="doc-h4">适合</h4>
                <ul class="doc-ul">
                  <li v-for="item in AUDIENCE.fit" :key="item">{{ item }}</li>
                </ul>
              </div>
              <div class="panel aud-panel">
                <h4 class="doc-h4">不适合（这些需求请选组织级平台）</h4>
                <ul class="doc-ul">
                  <li v-for="item in AUDIENCE.unfit" :key="item">{{ item }}</li>
                </ul>
              </div>
            </div>

            <h3 class="doc-h3">运行形态</h3>
            <ul class="doc-ul">
              <!-- 正文含本机路径与命令的内联代码，数据在同目录 productHelpContent.ts 内静态维护 -->
              <li v-for="fact in RUNTIME_FACTS" :key="fact" v-html="fact" />
            </ul>

            <h3 class="doc-h3">和常见方案的差异</h3>
            <ul class="doc-ul">
              <li v-for="line in POSITIONING" :key="line">{{ line }}</li>
            </ul>
          </div>
        </el-tab-pane>

        <!-- ==================== 2. 主要功能 ==================== -->
        <el-tab-pane label="主要功能" name="features">
          <div class="doc-body doc-body-wide">
            <h3 class="doc-h3">按测试生命周期拆开</h3>
            <table class="doc-table">
              <thead>
                <tr>
                  <th class="col-stage">阶段</th>
                  <th>要回答的问题</th>
                  <th class="col-entries">用到的入口</th>
                  <th>产出物</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in LIFECYCLE" :key="row.phase">
                  <td class="col-stage">{{ row.phase }}</td>
                  <td>{{ row.question }}</td>
                  <td class="col-entries">{{ row.entries }}</td>
                  <td>{{ row.artifact }}</td>
                </tr>
              </tbody>
            </table>

            <h3 class="doc-h3 doc-h3-gap">与侧边导航一一对应</h3>
            <p class="doc-p">顺序即侧栏顺序：入口 → 智能辅助 → 待办 → 预案 → 资产 → 结论 → 手段 → 沉淀 → 配置 → 帮助。</p>
            <section v-for="group in FEATURE_GROUPS" :key="group.group" class="doc-group">
              <h4 class="doc-h4">{{ group.group }}</h4>
              <table class="doc-table">
                <thead>
                  <tr>
                    <th class="col-entry">入口</th>
                    <th class="col-path">路径</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in group.entries" :key="item.entry + item.path">
                    <td class="col-entry">{{ item.entry }}</td>
                    <td class="col-path"><code class="text-mono">{{ item.path }}</code></td>
                    <!-- 说明含接口路径与文件名原文，数据在同目录 productHelpContent.ts 内静态维护 -->
                    <td v-html="item.desc" />
                  </tr>
                </tbody>
              </table>
            </section>
          </div>
        </el-tab-pane>

        <!-- ==================== 3. 价值点 ==================== -->
        <el-tab-pane label="价值点" name="value">
          <div class="doc-body doc-body-wide">
            <p class="doc-p">
              不写「更快更省」这类无法验证的话：每条价值点给出面向的角色、过去的成本、用了之后的差别，
              以及一次就能做完整的验证动作。
            </p>
            <section v-for="item in VALUE_ITEMS" :key="item.title" class="panel value-card">
              <h4 class="value-card__title">{{ item.title }}</h4>
              <p class="value-card__role">{{ item.role }}</p>
              <div class="value-card__rows">
                <div class="value-row">
                  <span class="value-row__label">过去</span>
                  <span class="value-row__text">{{ item.before }}</span>
                </div>
                <div class="value-row">
                  <span class="value-row__label">之后</span>
                  <span class="value-row__text">{{ item.after }}</span>
                </div>
                <div class="value-row">
                  <span class="value-row__label">怎么验</span>
                  <span class="value-row__text">{{ item.prove }}</span>
                </div>
              </div>
            </section>
          </div>
        </el-tab-pane>

        <!-- ==================== 4. 特色能力 ==================== -->
        <el-tab-pane label="特色能力" name="highlight">
          <div class="doc-body doc-body-wide">
            <p class="doc-p">这些能力组合在一起，是通用测试管理工具或单个大模型对话给不了的：</p>
            <section v-for="group in HIGHLIGHT_GROUPS" :key="group.group" class="doc-group">
              <h4 class="doc-h4">{{ group.group }}</h4>
              <div class="hl-grid">
                <div v-for="item in group.items" :key="item.title" class="panel hl-card">
                  <h5 class="hl-card__title">{{ item.title }}</h5>
                  <p class="hl-card__desc">{{ item.desc }}</p>
                  <p class="hl-card__edge">{{ item.edge }}</p>
                </div>
              </div>
            </section>
          </div>
        </el-tab-pane>

        <!-- ==================== 5. 使用说明 ==================== -->
        <el-tab-pane label="使用说明" name="guide">
          <div class="doc-body doc-body-wide">
            <h3 class="doc-h3">① 启动与停止</h3>
            <ol class="doc-ol">
              <li>Windows 双击 <code class="text-mono">start.bat</code>（或命令行执行）；macOS / Linux 运行 <code class="text-mono">./start.sh</code>。</li>
              <li>首次运行会创建 <code class="text-mono">.venv</code>、安装依赖、构建前端，随后自动打开 <code class="text-mono">http://127.0.0.1:8760</code>。</li>
              <li>脚本先检查端口：AITP 已在运行则直接打开页面；端口被其它程序占用会明确报错退出。可用参数：<code class="text-mono">--build</code>（强制重建前端）、<code class="text-mono">--reinstall</code>（重建虚拟环境）、<code class="text-mono">--no-browser</code>（不自动开浏览器）。</li>
              <li>停止服务：Windows <code class="text-mono">stop.bat</code>，macOS / Linux <code class="text-mono">./stop.sh</code>。</li>
              <li>升级：拉新代码后再跑一次 <code class="text-mono">start.bat --build</code>；数据库迁移在启动时按序自动执行，不需要手动跑 SQL。</li>
              <li>首次启动（空数据库）首页会自动弹出四步引导：配置 AI 模型 → 载入示例项目 → 跑一次 AI 生成 → 看报告与追溯；可跳过，完成或跳过后不再自动弹出。存量数据库升级默认不弹引导。</li>
              <li>引导里的「载入示例项目」以追加方式注入 <code class="text-mono">DEMO-EC</code> 示例（需求 / 用例 / 缺陷 / 知识 / 报告，外加可执行的接口断言脚本 <code class="text-mono">AUTO-API-001</code>），只重建示例自身、不动既有数据；项目列表空态也能一键载入。</li>
              <li>想重看引导，点本页右上角「重新打开首启引导」即可回到首页重新弹出。</li>
            </ol>

            <h3 class="doc-h3">② 配置 AI 档案（设置 → AI 设置）</h3>
            <ol class="doc-ol">
              <li>新增供应商档案：填 base URL、模型与 API Key；Key 保存进操作系统密钥库，任何接口都不回传明文。</li>
              <li>调整档案优先级（数值越大越优先）；本地与云端档案混排，首选失败自动降级到下一候选。</li>
              <li>用「自检」做真实调用验证：返回 ✓/✗、延迟、回复片段与中文可操作提示；未保存的配置与临时 Key 也能直接自检。</li>
              <li>本地大模型：启动 <code class="text-mono">ollama serve</code> 后选 <code class="text-mono">ollama_native</code> 档案，「模型列表」可直接拉取本机模型。</li>
              <li>外发脱敏：开启后发往云端模型的内容默认替换手机号、身份证、邮箱、银行卡号、API Key 与口令，点「预览」可以看到将要发出去的完整正文。</li>
              <li>不配任何模型也能用：能力会按本地规则降级并如实标注，不静默失败。也可让环境变量生效（参考 <code class="text-mono">.env.example</code>）。</li>
            </ol>

            <h3 class="doc-h3">③ 建项目、版本与功能点</h3>
            <ol class="doc-ol">
              <li>「测试资产 → 项目」新建项目，用顶栏的项目切换器切到当前项目——后续所有资产都挂在当前项目 + 版本下。</li>
              <li>「测试资产 → 项目版本管理」为项目建版本（版本名称与版本号分列维护）；版本被需求、用例、报告等引用时不能删，需先迁移或删除引用它的资产。</li>
              <li>「设置 → 模块树设置」维护功能点层级，最深三层（模块 → 子模块 → 功能），可为功能节点单独绑定 AI 能力与专属提示词。</li>
              <li>「设置 → 测试环境配置」登记 dev / test / staging 等环境档案；执行时以档案为唯一环境来源，连接信息加密只留本机、不会发给大模型，生产环境发起轮次前会二次确认。</li>
            </ol>

            <h3 class="doc-h3">④ 主流程：需求 → 知识沉淀</h3>
            <ol class="doc-ol">
              <li><strong>需求</strong>：在「测试资产 → 需求」录入或维护，关联模块与版本，状态在草稿 / 评审中 / 已通过 / 已实现 / 已否决之间流转；可用 AI「需求确认」先检查是否清晰可测。</li>
              <li><strong>测试点</strong>：AI 实验室 →「测试点脑图」从需求拆出大纲，可导出 <code class="text-mono">.xmind</code>。</li>
              <li><strong>用例</strong>：「用例生成」或「XMind 转用例」产出用例，产出区点「采纳为用例」落进「测试用例」，状态为「未执行」。</li>
              <li><strong>执行轮次</strong>：「测试预案 → 测试计划」建计划圈定用例，点「发起轮次」创建轮次（用例快照冻结、绑定环境，轮次状态为 待开始 / 执行中 / 已完成 / 已中止）；轮次详情逐条或批量录入通过 / 失败 / 阻塞 / 跳过，接口用例可「批量执行接口用例」，自动化脚本执行结果按用例编号或标签自动回写。已完成的轮次结果已冻结，不能再追加用例也不能再改结果，要重跑就另起一轮。</li>
              <li><strong>缺陷</strong>：轮次里的失败行点「提缺陷」，标题、步骤、实际结果自动预填并双向关联；缺陷按状态机流转（新建 → 已指派 → 已解决 → 待验证 → 关闭，可重新打开或拒绝），优先级分 P0–P3。</li>
              <li><strong>回归</strong>：「测试预案 → 回归清单」手工写或点「AI 生成回归点」，明细逐条回写通过 / 失败 / 阻塞 / 跳过 / 未执行，整单状态由明细回推。</li>
              <li><strong>准入</strong>：「质量分析 → 追溯与准入」查需求覆盖与遗留缺陷，切「发布准入」按阈值算机器结论与阻断原因，可先「预览结论」再点「留存准入记录」存档。</li>
              <li><strong>报告</strong>：「质量分析 → 测试报告」按实时数据生成草稿，补充章节后发布，导出 Markdown / HTML / PDF，或用「打印视图」直接出纸。</li>
              <li><strong>沉淀</strong>：有价值的产出点「加入知识库」进待审池，审核通过后成为正式条目，之后知识库问答助手能带着引用回答它。</li>
            </ol>

            <h3 class="doc-h3">⑤ 导入与导出</h3>
            <ul class="doc-ul">
              <li>用例支持 xlsx 导入：表头缺失整批拒绝并给行号；重复编号会提示「自动重排编号后导入」。</li>
              <li>「设置 → 测试文档模板设置」上传 docx / xlsx / csv / md / txt / json（≤5MB）即在本机解析出正文、列名与字体字号；原件留在本机可从「模板下载」列原样取回，换文件与删除模板会清掉旧原件，只上传没保存的文件在关闭弹窗后自动清理。</li>
              <li>需求、用例、缺陷、知识、任务清单等列表支持 CSV / JSON 导出，导出遵循列表当前筛选条件，不是全量。</li>
              <li>报告可导出 Markdown、HTML、PDF 三种文件，另有「打印视图」用于浏览器直接打印；趋势与版本对比插入章节后一并导出。</li>
              <li>列表页的筛选与分页写进地址栏，链接可直接分享，刷新后视图还原。</li>
            </ul>

            <h3 class="doc-h3">⑥ 备份与还原（设置 → 数据备份与还原设置）</h3>
            <ol class="doc-ol">
              <li>「立即备份」生成库快照（.db）；「立即整包备份」生成 zip，含库 + 附件 + 脚本 + 生成物 + 密钥引用 + 知识正文。</li>
              <li>可开启每天 / 每周自动整包备份：保留最近 1–90 份（默认 7 份），只清理最旧的自动备份；磁盘剩余低于保留空间（默认 512 MB，填 0 表示不检查）时跳过并告警。</li>
              <li>恢复自动先留 pre-restore 安全备份，就地生效无需重启；高版本 schema 的备份会被拒绝导入。</li>
              <li>换机迁移：资产都能恢复，只有 AI 密钥需要重新填一次（密钥绑定本机用户，这是安全设计）——到新机「设置 → AI 设置」重新「设置 Key」即可。</li>
            </ol>

            <h3 class="doc-h3">⑦ 用得顺的几个习惯</h3>
            <ul class="doc-ul">
              <li>一个版本开工前先在顶栏切好项目与版本筛选，之后列表、统计和采纳动作都跟这个口径走。</li>
              <li>AI 产出别急着采纳，先到「AI 实验室 → 执行记录」看引用核对与风险点；涉及 P0/P1 或批准性结论的，按提示复核后再用。</li>
              <li>改过提示词正文后到「验证沙箱」跑一次全量验证，用夹具案例确认行为没漂移。</li>
              <li>批量执行、脚本、备份这类长耗时操作丢进「任务队列」，重启后排队任务继续、遗留的执行中状态会自动对账。</li>
              <li>顶栏调色盘有 10 套主题，深色三套适合长时间盯屏；主题与首页卡片布局记在浏览器本地，换机器需重设。</li>
            </ul>
          </div>
        </el-tab-pane>

        <!-- ==================== 6. Demo 示例 ==================== -->
        <el-tab-pane :label="`Demo 示例（${DEMO_CASES.length}）`" name="demo">
          <div class="doc-body doc-body-wide">
            <p class="doc-p">
              以下 {{ DEMO_CASES.length }} 个示例按场景域分组，均按真实界面的菜单名、按钮名与列名描述，可照着一步步走通；
              每个示例的「预期结果」就是它自己的验收标准。
            </p>
            <section v-for="scene in demoScenes" :key="scene.name" class="doc-group">
              <h4 class="scene-title">{{ scene.name }}</h4>
              <div v-for="demo in scene.items" :key="demo.title" class="panel demo-card">
                <h5 class="demo-card__title">示例 {{ demo.index }}：{{ demo.title }}</h5>
                <p class="doc-p demo-card__intro">{{ demo.intro }}</p>
                <h6 class="demo-card__sub">操作步骤</h6>
                <ol class="doc-ol">
                  <li v-for="step in demo.steps" :key="step">{{ step }}</li>
                </ol>
                <h6 class="demo-card__sub">预期结果</h6>
                <ul class="doc-ul">
                  <!-- 个别示例给出接口路径原文，数据在同目录 productHelpContent.ts 内静态维护 -->
                  <li v-for="expect in demo.expects" :key="expect" v-html="expect" />
                </ul>
              </div>
            </section>
          </div>
        </el-tab-pane>

        <!-- ==================== 7. 作者信息 ==================== -->
        <el-tab-pane label="作者信息" name="author">
          <div class="doc-body">
            <p class="doc-p">
              <BrandName size="small" /> 由个人独立设计与维护，定位为「测试工程师自己每天要用」的本地工具，
              功能取舍都服从这条闭环：<strong>一个版本能在本机从头到尾测完，并且拿得出证据</strong>。
            </p>
            <el-descriptions :column="1" border size="small" class="author-desc">
              <el-descriptions-item label="产品名称">
                <BrandName size="small" />（AITP）
              </el-descriptions-item>
              <el-descriptions-item label="产品定位">单用户、本地优先、离线可用的 AI 测试工作台</el-descriptions-item>
              <el-descriptions-item label="作者 / 维护者">mikiyi（yichengren）</el-descriptions-item>
              <el-descriptions-item label="应用版本">1.0.0（取自后端 <code class="text-mono">backend/aitp/db/migrator.py</code> 的 APP_VERSION）</el-descriptions-item>
              <el-descriptions-item label="后端技术栈">Python 3.11+ · Flask · SQLite（参数化仓储层）· SSE 流式输出</el-descriptions-item>
              <el-descriptions-item label="前端技术栈">Vue 3 + TypeScript · Element Plus · Pinia · ECharts · Vite</el-descriptions-item>
              <el-descriptions-item label="运行环境要求">Windows / macOS / Linux；Python 3.11+；Node.js 20+（仅首次构建前端需要）；现代浏览器</el-descriptions-item>
              <el-descriptions-item label="服务地址"><code class="text-mono">http://127.0.0.1:8760</code>（默认仅本机可访问）</el-descriptions-item>
              <el-descriptions-item label="数据位置">
                库 <code class="text-mono">data/aitp.db</code> · 知识 <code class="text-mono">knowledge/</code> · 备份 <code class="text-mono">data/backups/</code> · 密钥存操作系统密钥库
              </el-descriptions-item>
              <el-descriptions-item label="皮肤">内置 10 套主题（含黑 / 科技 / 工业 3 套深色），入口在顶栏</el-descriptions-item>
              <el-descriptions-item label="开源共建">
                欢迎开源社区的各位热心开发者——包括但不限于软件产品老师、软件开发老师、软件测试老师、软件运维老师——
                一起来找出产品缺陷与待改进之处，一起维护、补强产品功能，让
                <BrandName size="small" /> 日趋完善、好用。
              </el-descriptions-item>
              <el-descriptions-item label="联系与反馈">
                本产品未内置对外联系方式；问题反馈与需求建议请通过仓库 Issue 或维护者渠道提交。
              </el-descriptions-item>
            </el-descriptions>
            <p class="muted doc-p">
              本页为纯静态文档，正文数据集中在 <code class="text-mono">frontend/src/views/settings/productHelpContent.ts</code>；
              内容口径以 <code class="text-mono">frontend/src/router/menu.ts</code> 导航结构与后端能力目录为准，
              更深入的功能地图与安全边界说明见仓库根目录 <code class="text-mono">README.md</code> 与 <code class="text-mono">docs/</code> 目录。
            </p>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 「帮助文档」：纯静态文档页，不请求任何后端接口。
 * 正文数据与口径集中在同目录的 productHelpContent.ts，本文件只负责排版。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Guide, MagicStick } from '@element-plus/icons-vue'
import BrandName from '@/components/BrandName.vue'
import type { DemoCase } from './productHelpContent'
import {
  AUDIENCE,
  DEMO_CASES,
  FEATURE_GROUPS,
  HIGHLIGHT_GROUPS,
  LIFECYCLE,
  LOOP_NODES,
  PAIN_POINTS,
  POSITIONING,
  PRODUCT_STATS,
  RUNTIME_FACTS,
  VALUE_ITEMS,
} from './productHelpContent'

const activeTab = ref('intro')

const router = useRouter()

/** 回首启引导：重置服务端状态并回到首页弹出引导（HomeView 监听该事件）。 */
async function reopenTour(): Promise<void> {
  await router.push('/')
  window.dispatchEvent(new CustomEvent('aitp:reopen-tour'))
}

type NumberedDemo = DemoCase & { index: number }

/** Demo 按场景域分组，序号沿用全局顺序，便于示例之间互相引用。 */
const demoScenes = computed(() => {
  const groups: { name: string; items: NumberedDemo[] }[] = []
  DEMO_CASES.forEach((demo, index) => {
    const item: NumberedDemo = { ...demo, index: index + 1 }
    const hit = groups.find((group) => group.name === demo.scene)
    if (hit) hit.items.push(item)
    else groups.push({ name: demo.scene, items: [item] })
  })
  return groups
})
</script>

<style scoped>
.doc-body {
  max-width: 920px;
}

/* 表格类板块稍宽，避免说明列被挤到频繁换行 */
.doc-body-wide {
  max-width: 1120px;
}

.doc-h3 {
  margin: 20px 0 10px;
  font-size: 15px;
}

.doc-h3:first-child {
  margin-top: 0;
}

.doc-h3-gap {
  margin-top: 26px;
}

.doc-h4 {
  margin: 14px 0 6px;
  font-size: 14px;
}

.doc-p {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.8;
}

.doc-ul,
.doc-ol {
  margin: 0 0 12px;
  padding-left: 22px;
  font-size: 13px;
  line-height: 1.9;
}

.doc-ul code,
.doc-ol code {
  color: var(--aitp-text);
}

.doc-group {
  margin-bottom: 18px;
}

/* 闭环链 */
.chain {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 12px 0;
}

.chain__node {
  padding: 4px 12px;
  border: 1px solid var(--aitp-border);
  border-radius: 999px;
  background: var(--aitp-primary-soft);
  font-size: 12px;
  color: var(--aitp-text);
}

.chain__arrow {
  font-size: 12px;
  color: var(--aitp-text-weak);
}

/* 表格 */
.doc-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  line-height: 1.7;
  table-layout: fixed;
}

.doc-table th,
.doc-table td {
  padding: 8px 10px;
  border: 1px solid var(--aitp-border);
  text-align: left;
  vertical-align: top;
  word-break: break-word;
}

.doc-table th {
  background: var(--aitp-subtle);
  font-weight: 600;
}

.doc-table td {
  color: var(--aitp-text);
}

.doc-table td code {
  color: var(--aitp-text);
}

.col-entry {
  width: 168px;
}

.col-path {
  width: 168px;
}

.col-stage {
  width: 76px;
}

.col-entries {
  width: 240px;
}

.cell-where {
  margin-top: 6px;
  font-size: 12px;
  color: var(--aitp-primary);
}

/* 产品规模表 */
.stats-table td {
  border-left: none;
  border-right: none;
}

.stats-table tr:first-child td {
  border-top: none;
}

.stats-label {
  width: 200px;
  color: var(--aitp-text-weak);
}

.stats-value {
  width: 200px;
  font-weight: 600;
  color: var(--aitp-primary);
}

/* 适合 / 不适合 */
.aud-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.aud-panel {
  padding: 10px 14px;
}

.aud-panel .doc-ul {
  margin-bottom: 0;
}

/* 价值点卡片 */
.value-card {
  padding: 12px 14px;
  margin-bottom: 12px;
}

.value-card__title {
  margin: 0 0 2px;
  font-size: 14px;
  color: var(--aitp-primary);
}

.value-card__role {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.value-card__rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.value-row {
  display: flex;
  gap: 8px;
  font-size: 13px;
  line-height: 1.8;
}

.value-row__label {
  flex: 0 0 52px;
  font-size: 12px;
  color: var(--aitp-text-weak);
}

.value-row__text {
  flex: 1;
  color: var(--aitp-text);
}

/* 特色能力卡片 */
.hl-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
}

.hl-card {
  padding: 12px 14px;
}

.hl-card__title {
  margin: 0 0 6px;
  font-size: 13px;
  color: var(--aitp-primary);
}

.hl-card__desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--aitp-text);
}

.hl-card__edge {
  margin: 8px 0 0;
  padding-top: 8px;
  border-top: 1px dashed var(--aitp-border);
  font-size: 12px;
  line-height: 1.7;
  color: var(--aitp-text-weak);
}

/* Demo 卡片 */
.scene-title {
  margin: 18px 0 8px;
  font-size: 14px;
  color: var(--aitp-primary);
}

.demo-card {
  padding: 14px 16px;
  margin-bottom: 14px;
}

.demo-card__title {
  margin: 0 0 8px;
  font-size: 14px;
}

.demo-card__intro {
  color: var(--aitp-text-weak);
}

.demo-card__sub {
  margin: 10px 0 4px;
  font-size: 12px;
  color: var(--aitp-text-weak);
  font-weight: 600;
}

/* 作者信息 */
.author-desc {
  margin: 12px 0;
}
</style>
