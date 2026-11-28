"""评测子包：纯领域层（指标、判定、门禁、统计检验）。

这一层不碰数据库、不发网络请求，所有函数都能被 `backend/scripts/check_eval_*.py`
直接调用；落库与跑批在 `aitp/services/eval_service.py` / `eval_runner.py`。
"""

from .ablation import (
    assert_comparable,
    mcnemar_pvalue,
    minimum_detectable_effect,
    paired_flips,
    paired_test,
)
from .cases import (
    ALLOWED_ASSERTIONS,
    CATEGORIES,
    SEVERITIES,
    evaluate_assertions,
    next_case_no,
    normalize_case,
)
from .gate import CONCLUSION_ORDER, DEFAULT_THRESHOLDS, evaluate_gate
from .judges import (
    calibration_summary,
    cohen_kappa,
    confusion_matrix,
    double_judge,
    panel_decision,
    parse_judge_verdict,
    position_bias,
    verbosity_bias,
)
from .metrics import (
    METRIC_KEYS,
    compute_metrics,
    percentile,
    ratio_metric,
    severity_weighted_error,
    wilson_interval,
)
from .rag import faithfulness, macro_ndcg, macro_recall, ndcg_at_k, recall_at_k
from .rubric import score_rubric, validate_rubric
from .severity import SEVERITY_WEIGHTS, weight_of
from .traces import evaluate_end_state, evaluate_trace, tool_prf

__all__ = [
    "ALLOWED_ASSERTIONS",
    "CATEGORIES",
    "CONCLUSION_ORDER",
    "DEFAULT_THRESHOLDS",
    "METRIC_KEYS",
    "SEVERITIES",
    "SEVERITY_WEIGHTS",
    "assert_comparable",
    "calibration_summary",
    "cohen_kappa",
    "compute_metrics",
    "confusion_matrix",
    "double_judge",
    "evaluate_assertions",
    "evaluate_end_state",
    "evaluate_gate",
    "evaluate_trace",
    "faithfulness",
    "macro_ndcg",
    "macro_recall",
    "mcnemar_pvalue",
    "minimum_detectable_effect",
    "ndcg_at_k",
    "next_case_no",
    "normalize_case",
    "paired_flips",
    "paired_test",
    "panel_decision",
    "parse_judge_verdict",
    "percentile",
    "position_bias",
    "ratio_metric",
    "recall_at_k",
    "score_rubric",
    "severity_weighted_error",
    "tool_prf",
    "validate_rubric",
    "verbosity_bias",
    "weight_of",
    "wilson_interval",
]
