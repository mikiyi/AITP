# eval_toolkit：Promptfoo 对拍 + Ragas 最小 demo

配套 `EVAL.md` 的动手验证工具，**离线可跑**，不花 token、不需要 API key。

## 跑法

```bash
# 1) 生成 promptfoo 配置（12 条金融用例 × good/bad）
python tools/eval_toolkit/build_config.py

# 2) 跑 promptfoo（需要 node）
cd tools/eval_toolkit/promptfoo
npx --yes promptfoo eval -c promptfooconfig.yaml --no-progress-bar -o out.json
cd -

# 3) 与 AITP 引擎对拍（用项目 venv）
python tools/eval_toolkit/compare.py

# 4) RAG 最小评测
python tools/eval_toolkit/ragas_min_demo.py
```

## 预期结果

- promptfoo：12 passed / 12 failed（24 个 prompt = 12 用例 × good/bad）
- 对拍：24/24 = 100% 一致（规则断言口径对齐）
- 引擎额外给出：Wilson 区间、风险加权错误率、样本分级结论
- ragas demo：无来源断言被判无支撑（faithfulness 下降）

## 这套东西证明什么

1. 断言语义能与 Promptfoo 对齐（24/24）；
2. 知道框架边界：点估计 vs 区间下界、无风险分级、无 invalid 语义；
3. RAG 指标能讲清：faithfulness 是 claim 级判定，precision 与 recall 会背离。
