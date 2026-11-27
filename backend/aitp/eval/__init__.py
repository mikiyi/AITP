"""评测子包：纯领域层（指标、用例校验、门禁求值）。

这一层不碰数据库、不发网络请求，所有函数都能被 `backend/scripts/check_eval_metrics.py`
直接调用；落库与跑批在 `aitp/services/eval_service.py`。
"""

from .cases import (
    ALLOWED_ASSERTIONS,
    CATEGORIES,
    SEVERITIES,
    evaluate_assertions,
    next_case_no,
    normalize_case,
)
from .gate import CONCLUSION_ORDER, DEFAULT_THRESHOLDS, evaluate_gate
from .metrics import (
    SEVERITY_WEIGHTS,
    compute_metrics,
    percentile,
    ratio_metric,
    severity_weighted_error,
    wilson_interval,
)

__all__ = [
    "ALLOWED_ASSERTIONS",
    "CATEGORIES",
    "SEVERITIES",
    "CONCLUSION_ORDER",
    "DEFAULT_THRESHOLDS",
    "SEVERITY_WEIGHTS",
    "compute_metrics",
    "evaluate_assertions",
    "evaluate_gate",
    "next_case_no",
    "normalize_case",
    "percentile",
    "ratio_metric",
    "severity_weighted_error",
    "wilson_interval",
]
