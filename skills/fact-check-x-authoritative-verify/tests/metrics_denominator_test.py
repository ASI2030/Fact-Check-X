#!/usr/bin/env python3
"""覆盖率分母与分子口径回归。

对应徐博 2026-09-12 反馈：某个知识点在所有平台都是「未覆盖」或「证据不足」时，
它不代表任何一家的答题表现，不得进入覆盖率与准确率分母；而答到但证据不足，
仍然算这一家覆盖到了。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from render_final_report import metrics

PLATFORMS = [{"platform": "alpha"}, {"platform": "beta"}]


def point(point_id: str, role: str, per_platform: dict[str, tuple[bool, str]]) -> dict:
    return {
        "id": point_id,
        "role": role,
        "claims": {pid: {"covered": covered} for pid, (covered, _) in per_platform.items()},
        "authority": {
            "verdicts": {pid: {"category": category} for pid, (_, category) in per_platform.items()}
        },
    }


points = [
    # 两家都答到且可裁决：正常计入
    point("K1", "direct", {"alpha": (True, "direct_accurate"), "beta": (True, "misleading")}),
    # alpha 可裁决、beta 答到但证据不足：该点保留，且 beta 仍算覆盖
    point("K2", "direct", {"alpha": (True, "direct_accurate"), "beta": (True, "unverified")}),
    # 全员无效（一家未覆盖、一家证据不足）：整体退出分母
    point("K3", "direct", {"alpha": (False, "omitted"), "beta": (True, "unverified")}),
    # 全员未覆盖：整体退出分母
    point("K4", "direct", {"alpha": (False, "omitted"), "beta": (False, "omitted")}),
    # 补充参考不参与直接答案指标
    point("K5", "reference", {"alpha": (True, "direct_accurate"), "beta": (False, "omitted")}),
]

result = metrics(points, PLATFORMS)

# 合法直接答案知识点只剩 K1、K2：两家都答到了，覆盖率必须是 100%
# 修复前 K3/K4 留在分母、且答到但证据不足的 K2 不计分子，两家都拿不到 100%
assert result["alpha"]["覆盖率"] == 1.0, result["alpha"]["覆盖率"]
assert result["beta"]["覆盖率"] == 1.0, result["beta"]["覆盖率"]

excluded = result["alpha"]["未计入直接知识点"]
assert sorted(excluded) == ["K3", "K4"], excluded
assert result["beta"]["未计入直接知识点"] == excluded
assert result["alpha"]["遗漏率"] == 0.0
assert result["beta"]["遗漏率"] == 0.0

# 覆盖率与遗漏率互补
for pid in ("alpha", "beta"):
    total = result[pid]["覆盖率"] + result[pid]["遗漏率"]
    assert abs(total - 1.0) < 1e-9, (pid, total)

# 准确率分母仍排除证据不足：beta 只有 K1（misleading）可计分
assert result["beta"]["准确率"] == 0.0, result["beta"]["准确率"]
assert result["beta"]["幻觉率"] == 1.0, result["beta"]["幻觉率"]
# beta 答到 K1、K2 两点，其中 1 点可裁决
assert abs(result["beta"]["证据充分率"] - 0.5) < 1e-9, result["beta"]["证据充分率"]
# alpha 两点均可裁决且正确
assert result["alpha"]["准确率"] == 1.0
assert result["alpha"]["证据充分率"] == 1.0

# 一家未覆盖时遗漏率要如实体现
missing = [
    point("K1", "direct", {"alpha": (True, "direct_accurate"), "beta": (False, "omitted")}),
    point("K2", "direct", {"alpha": (True, "direct_accurate"), "beta": (True, "direct_accurate")}),
]
partial = metrics(missing, PLATFORMS)
assert partial["beta"]["覆盖率"] == 0.5, partial["beta"]["覆盖率"]
assert partial["beta"]["遗漏率"] == 0.5

print("PASS 覆盖率分母剔除全员无效知识点；答到即算覆盖")
