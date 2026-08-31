#!/usr/bin/env python3
"""
Fact-Check-X 各方答案测评报告渲染器。

稳定指标口径：
  分母 N = 合法直接答案知识点（剔除补充参考 + 剔除编造）
  覆盖率 + 遗漏率 = 100%（分母 N）
  可裁决的覆盖类下：准确率 + 幻觉率 = 100%（分母 = 已有足够证据裁决的直接答案覆盖数）
    准确率 = 直接准确（官方依据，含算术级显然推导 2000×1.4=2800）+ 间接准确（非官方+官方验证通过）
    幻觉率 = 巧合式（无引用依据但与官方依据巧合一致）+ 误导式（结果错）
  编造（官方查无）独立轴，不计覆盖/准确/幻觉；直接答案与补充参考始终分区呈现
  直接答案区只有 4 标签 + 灰色未覆盖；补充参考单独保留证据价值与风险

溯源与复现：
  ① 每个知识点判定带「所附依据原文」+「官方验证依据原文」
  ② 原始答案 + 参考文献全文存证（原始凭证，防"我们没这么答过"）
  ③ 原始答案与参考文献全文存证
  ④ 指标口径速查 + ⑤ 评测元信息（可复现性）
"""
import argparse, json, os, subprocess
from datetime import datetime
from pathlib import Path
from html import escape
from urllib.parse import urlparse

def _safe_href(u):
    """只允许 http(s)/mailto 进入 href，丢弃 javascript:/data: 等危险协议（防报告内 XSS）。"""
    s = (u or "").strip()
    return escape(s) if s.lower().startswith(("http://", "https://", "mailto:")) else ""

p = argparse.ArgumentParser()
p.add_argument('--analysis', default='/tmp/fact-check-x-platform-analysis.json')
p.add_argument('--result', default='/tmp/fc_result.json')
p.add_argument('--topic', default='platform-report')
p.add_argument('--out', default='')
p.add_argument('--no-open', action='store_true')
a = p.parse_args()

A = json.load(open(a.analysis))
R = json.load(open(a.result)) if Path(a.result).exists() else {}

query = str(A.get('query') or '?').strip()
N_list = A.get('answer_knowledge_points', [])
SE = A.get('side_evaluation', {})

# 兼容旧分析：若 K 未带 official_basis（新引擎才生成），从 side_evaluation 现场派生一份
#   官方验证/真值/阴性证据本就是「全家共用一份」，取首个 covered 侧即可。
for _k in N_list:
    if _k.get('official_basis'):
        continue
    _kid = _k.get('id')
    for _s in ('dknow', 'doubao'):
        _e = (SE.get(_s, {}) or {}).get(_kid, {}) or {}
        if not _e.get('covered'):
            continue
        _pv = _e.get('provenance_verify') or {}
        _ne = _e.get('null_evidence') or {}
        if _pv.get('excerpt') or _pv.get('gov_url') or _pv.get('source_id', '—') not in ('—', '', None) \
           or _ne.get('silent_clause_excerpt') or _ne.get('governing_doc_gov_urls'):
            _k['official_basis'] = {
                'verify_result': _pv.get('result', _e.get('verify_result', '')),
                'source_id': _pv.get('source_id', '—'), 'gov_url': _pv.get('gov_url', ''),
                'excerpt': _pv.get('excerpt', ''), 'truth_state_K': _e.get('truth_state_K', ''),
                'ts_confidence': _e.get('ts_confidence'), 'null_evidence': _ne,
            }
            break
M = A.get('platform_metrics', {})
MT = A.get('metrics_by_tier', {})
H2H = A.get('head_to_head', {})
V = A.get('verdict', {})
ref = A.get('reference_analysis', {})
summary = A.get('summary', '')
findings = A.get('key_findings', [])
ai = A.get('ai_info', {})
SCRAPED = R.get('scraped', {})
HEALTH = A.get('llm_health', {})
APPEALS = A.get('appeals', [])
AUTHORITY_VERIFICATION_SHA256 = str(A.get('authority_verification_sha256') or '')

TIER_META = {  # 角色标签只保留核心 / 直接相关；补充参考另区。
    "core":    ("核心", "#1e3a8a", "用户问的那个裁定性核心点（能不能/多久/多少钱）"),
    "support": ("直接相关", "#6366f1", "直接回答用户所问的其它知识点"),
    "edge":    ("补充参考", "#9ca3af", "相关但非用户所问，不计覆盖率"),
}
def tier_of(k):
    t = k.get("tier", "support")
    return t if t in TIER_META else "support"

# ──────────────── 指标口径（单一事实源：tooltip 和附录共用） ────────────────
METRIC_DOC = {
    "覆盖率":   ("合法直接答案知识点 N", "答案合并去重后，先剔除「补充参考（相关但非用户所问）」、再剔除「编造（官方查无）」的合法直接答案知识点，该家答到了多少", "只衡量「直接回答用户所问的点答没答到」；补充参考答得再多、编得再多都不抬覆盖"),
    "遗漏率":   ("合法直接答案知识点 N", "该家没答到的合法直接答案知识点占比", "与覆盖率互补，相加=100%"),
    "准确率":   ("已有足够证据裁决的直接答案覆盖数", "可裁决答案里「有据且正确」的比例 = 直接准确 + 间接准确", "证据不足项单独展示，不按错误计分"),
    "直接准确率": ("已有足够证据裁决的直接答案覆盖数", "声明与其所附官方原站材料或官方来源一致；含算术级显然推导（如正文'2000上增加40%'→答案'2800'）", "最高可信级：主张由官方来源直接支持"),
    "间接准确率": ("已有足够证据裁决的直接答案覆盖数", "声明忠实于其他非官方材料，且事后经官方源独立验证正确", "内容正确，但答案自身所附出处不是官方来源"),
    "证据充分率": ("该家直接答案覆盖数", "已有足够权威证据完成裁决的覆盖项比例", "低于100%表示仍有证据边界，不代表对应回答错误"),
    "官方证据支持率": ("该家直接答案覆盖数", "该主张有经原文核对、确实支持它的所附官方材料", "可由局部角标或无角标时的回答级参考资料语义匹配建立"),
    "局部角标覆盖率": ("该家直接答案覆盖数", "该主张原答案片段内存在与它相连的可验证脚标", "只衡量逐句引用完整性；低于事实准确率不代表答案内容错误"),
    "幻觉率":   ("已有足够证据裁决的直接答案覆盖数", "可裁决答案里非「有据且正确」的比例 = 巧合式 + 误导式（编造已移出，独立成轴）", "与准确率互补"),
    "巧合式幻觉率": ("已有足够证据裁决的直接答案覆盖数", "声明无引用依据或与所附材料不符，但经官方源验证后与官方依据巧合一致", "结果正确但缺少可核验的引用支持，故不计入准确率"),
    "误导式幻觉率": ("已有足够证据裁决的直接答案覆盖数", "经官方源验证，声明的结果是错误的", "最危险的一类：用户照做会被误导"),
}
def tip(name):
    d = METRIC_DOC.get(name)
    return f' title="分母：{d[0]}。{d[1]}。{d[2]}"' if d else ''

# 类别 → 颜色/图标/解释/中性外显标签（对外用描述性语言，正式术语进 tooltip 避免被攻击）
CAT_STYLE = {
    # 直接答案区保持四类稳定标签。
    "直接准确": ("#059669", "✅", "与所附官方原站材料或官方来源一致（含算术级显然推导），可直接判定", "直接正确"),
    "间接准确": ("#10b981", "☑", "忠实于其他非官方材料，且经官方源独立验证通过", "间接正确"),
    "巧合式幻觉": ("#f59e0b", "🎲", "无引用依据或与所附材料不符，但经官方核验后与官方依据巧合一致", "无引用依据，但与官方依据巧合一致"),
    "误导式幻觉": ("#dc2626", "⚠", "经官方源验证，结果是错误的（用户照做会被误导）", "幻觉：严重误导"),
    # 编造（官方查无）→ 仅出现在补充参考分析的③凭空编造（不进直接答案区）
    "编造式幻觉": ("#b91c1c", "✖", "在官方原站和官方来源中均查不到、无法核验 = 凭空编造", "凭空编造·官方查无"),
    # 答案遗漏：非判定标签，灰色文本，与 4 个判定标签视觉隔离
    "证据不足": ("#64748b", "?", "当前权威证据不足，不能据此判定主张真假；该项不进入准确率分母", "证据不足"),
    "答案遗漏": ("#9ca3af", "·", "该家没有答到这个知识点（状态指示，非判定）", "未覆盖"),
}
def cat_norm(c):
    for k in CAT_STYLE:
        if c.startswith(k): return k
    # 历史标签兜底（旧数据兼容，统归到现行 5 类）
    if "诚实无规定" in c or "无据造规则" in c or "待核验" in c: return "编造式幻觉"
    if "证据不足" in c: return "证据不足"
    if "未核验" in c or "缺依据" in c: return "证据不足"
    if "猜测" in c: return "巧合式幻觉"
    if "捏造" in c or "疑似" in c: return "编造式幻觉"
    if "误导" in c: return "误导式幻觉"
    return "答案遗漏"

# 参与方动态解析（支持 N 家）：读 platform/participants.json，按分析里实际 side 排序取名/色
_PAL = ["#4f46e5", "#dc2626", "#0891b2", "#059669", "#d97706", "#7c3aed", "#0ea5e9"]
_FALLBACK = {"dknow": ("深知晓", "#4f46e5"), "doubao": ("豆包", "#dc2626")}
def _load_participants():
    p = Path(__file__).resolve().parent / "platform" / "participants.json"
    reg = {}
    if p.exists():
        try:
            for x in json.load(open(p)).get("participants", []):
                if x.get("key"):
                    reg[x["key"]] = (x.get("name", x["key"]), x.get("color", "#666"))
        except Exception:
            pass
    return reg
_PREG = _load_participants()
_PORDER = list(_PREG.keys())
def get_sides():
    keys = list((A.get("platform_metrics") or {}).keys()) or list((A.get("side_evaluation") or {}).keys()) or ["dknow", "doubao"]
    keys.sort(key=lambda k: (_PORDER.index(k) if k in _PORDER else 99, k))
    out = []
    for i, k in enumerate(keys):
        nm, co = _PREG.get(k) or _FALLBACK.get(k) or (k, _PAL[i % len(_PAL)])
        out.append((k, nm, co))
    return out
SIDE = get_sides()
PLATFORM_COUNT = len(SIDE)
PLATFORM_LAYOUT = "platform-layout-compact" if PLATFORM_COUNT <= 3 else "platform-layout-dense"
KP_MIN_WIDTH = 440 + 210 * PLATFORM_COUNT

# ──────────────── 来源可验证性标注 ────────────────
OFFICIAL_MEDIA = ("people.com.cn", "xinhuanet.com", "qstheory.cn", "gmw.cn")
DKNOW_OFFICIAL_SIDES = {"dknow", "dknowc-chat", "dknowc-deep-research"}
OFFICIAL_ORIGIN_KEYS = (
    "originUrl", "origin_url", "resourceUrl", "resource_url",
    "officialUrl", "official_url", "sourceUrl", "source_url",
)
def _official_url(url):
    host = (urlparse(url or "").hostname or "").lower()
    return bool(
        host == "gov.cn"
        or host.endswith(".gov.cn")
        or any(host == d or host.endswith("." + d) for d in OFFICIAL_MEDIA)
    )
def _http_url(url):
    parsed = urlparse(str(url or "").strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
def _official_origin(reference, side=""):
    if reference.get("originAttributionStatus") == "trusted_search_no_source_url":
        return ""
    for key in OFFICIAL_ORIGIN_KEYS:
        candidate = str(reference.get(key) or "").strip()
        if candidate and (
            _official_url(candidate)
            or (
                side in DKNOW_OFFICIAL_SIDES
                and reference.get("originAttributionStatus") == "trusted_search_official_url"
                and _http_url(candidate)
            )
        ):
            return candidate
    return ""
def source_kind(reference, side=""):
    url = str(reference.get("url") or "")
    host = (urlparse(url).hostname or "").lower()
    if side in DKNOW_OFFICIAL_SIDES and any(
        str(reference.get(key) or "").strip()
        for key in ("url", "webTitle", "title", "content", "snippet")
    ):
        return "dknow_trusted_search_official"
    if _official_url(url):
        return "official_site"
    return "non_official"
def src_badge(reference, side=""):
    kind = source_kind(reference, side)
    if kind == "official_site":
        return '<span class="bgov">官方原站</span>'
    if kind == "dknow_trusted_search_official":
        attribution = reference.get("originAttributionStatus")
        note = (
            "由深知可信搜索提供，统一按官方来源处理；已返回来源链接"
            if attribution == "trusted_search_official_url"
            else "官方来源"
        )
        return f'<span class="bdtv" title="{escape(note)}">官方来源</span>'
    return '<span class="bcom">非官方来源</span>'

def tier_breakdown(sk):
    """不渲染旧的三级分层小条；保留空函数兼容现有调用。"""
    return ""

def _lr100(fracs):
    """最大余数法：把一组分数取整为百分比，保证和 = round(总和*100)（三段闭合时=100）。"""
    target = round(sum(fracs) * 100)
    floors = [int(f * 100) for f in fracs]
    rem = target - sum(floors)
    order = sorted(range(len(fracs)), key=lambda i: -(fracs[i] * 100 - floors[i]))
    for i in order[:max(0, rem)]:
        floors[i] += 1
    return floors

def metric_card(sk, sname, color):
    m = M.get(sk, {})
    size = (ai.get(sk) or {}).get("size", "")
    def g(k): return round(m.get(k,0)*100)
    # 覆盖质量两段（准确/幻觉）最大余数法，相加恒=100%
    acc_i, hall_i = _lr100([m.get("准确率",0), m.get("幻觉率",0)])
    no_direct = ("_N" in m) and m.get("_N", 0) == 0   # 本题无直接答案知识点
    no_resolved = m.get("_resolved", 0) == 0
    fab_n = m.get("编造数", 0)
    ref_val = m.get("参考_有价值正确", 0); ref_hal = m.get("参考_幻觉式提醒", 0)
    cov_txt = '—' if no_direct else f'{g("覆盖率")}%'
    om_txt = '—' if no_direct else f'{g("遗漏率")}%'
    acc_txt = '—' if no_direct or no_resolved else f'{acc_i}%'
    hall_txt = '—' if no_direct or no_resolved else f'{hall_i}%'
    fab_block = (
        f'<div class="{"fabricated-alert" if fab_n else "fabricated-clear"}">'
        + (f'⚠ 高风险告警：检出编造 <b>{fab_n}</b> 项（官方查无、凭空捏造），详见编造清单'
           if fab_n else '✓ 未检出编造')
        + '</div>')
    ref_block = (
        f'<div style="margin-top:5px;padding:5px 8px;border-radius:5px;font-size:12px;'
        f'background:#f1f5f9;color:#475569">📎 补充参考（不计覆盖）：有价值正确 <b>{ref_val}</b> 项 · 幻觉式提醒 <b>{ref_hal}</b> 项</div>')
    return f'''
    <div class="mcard">
      <div class="mtitle" style="color:{color}">{sname} <span class="muted small">{escape(size)}</span></div>
      <div class="kpi-grid">
        <div class="kpi kpi-coverage"><span class="tt"{tip("覆盖率")}>覆盖率</span><b>{cov_txt}</b><small class="tt"{tip("遗漏率")}>遗漏率 {om_txt}</small></div>
        <div class="kpi kpi-accuracy"><span class="tt"{tip("准确率")}>准确率</span><b>{acc_txt}</b><small>有据且正确</small></div>
        <div class="kpi kpi-hallucination"><span class="tt"{tip("幻觉率")}>幻觉率</span><b>{hall_txt}</b><small>巧合 + 误导</small></div>
      </div>
      {f'<div class="muted small" style="color:#b45309;margin:2px 0 0">本题无可核验的直接答案知识点</div>' if no_direct else ''}
      <div class="m2">证据状态：<span class="tt"{tip("证据充分率")}>充分</span> <b>{g("证据充分率")}%</b> ｜ <span class="tt"{tip("官方证据支持率")}>官方支持</span> <b>{g("官方证据支持率")}%</b> ｜ <span class="tt"{tip("局部角标覆盖率")}>局部角标</span> <b>{g("局部角标覆盖率")}%</b></div>
      <table class="mtab">
        <tr><td class="tt"{tip("直接准确率")}>· 直接准确（官方依据）</td><td>{g("直接准确率")}%</td></tr>
        <tr><td class="tt"{tip("间接准确率")}>· 间接准确（非官方+官方验证）</td><td>{g("间接准确率")}%</td></tr>
        <tr class="hl"><td class="tt"{tip("巧合式幻觉率")}>· 巧合式幻觉（无引用依据但与官方依据巧合一致）</td><td>{g("巧合式幻觉率")}%</td></tr>
        <tr class="hl"><td class="tt"{tip("误导式幻觉率")}>· 误导式幻觉（结果错）</td><td>{g("误导式幻觉率")}%</td></tr>
      </table>
      {fab_block}
      {ref_block}
    </div>'''

VERDICT_STYLE = {
    "direct_supported":   ("#059669", "ti-shield-check", "全部有官方依据支持"),
    "indirect_supported": ("#0f766e", "ti-shield-check", "部分非官方依据支持，通过全知晓用官方依据的核验"),
    "supported":          ("#059669", "ti-shield-check", "全部有官方依据支持"),
    "error":         ("#dc2626", "ti-alert-triangle", "经核验有误"),
    "coincidental":  ("#d97706", "ti-dice", "无引用依据，但与官方依据巧合一致"),
    "missing":       ("#6b7280", "ti-minus", "未直接回答"),
}
def verdict_block():
    """只保留“核心问题 + 各家一句话结论”作为裁决上下文。"""
    if not V or not V.get("sides"): return ""
    cq = escape(V.get("core_question", query))
    cards = []
    for sk, sname, color in SIDE:
        sv = V["sides"].get(sk, {})
        lvl = sv.get("level", "missing")
        if lvl in ("honest_correct", "pending", "fabricated_rule", "unverified"):
            lvl = "missing"
        lcol, icon, ltag = VERDICT_STYLE.get(lvl, VERDICT_STYLE["missing"])
        head = escape(sv.get("headline", "—"))
        cards.append(
            f'<div class="vcard" style="border-left:4px solid {lcol}">'
            f'<div class="vside" style="color:{color}">{sname}</div>'
            f'<div class="vhead"><span class="vbadge" style="background:{lcol}">{ltag}</span> {head}</div>'
            f'</div>')
    return f'''
<div class="verdict">
  <div class="vq"><span class="vqlab">核心问题</span>{cq}</div>
  <div class="vcards">{"".join(cards)}</div>
  <div class="muted small" style="margin-top:6px">完整逐条判定见②直接答案区。</div>
</div>'''

def h2h_block():
    """正面交锋：只在两家都覆盖的 K 上对比，去掉'谁挑了更易子集'的偏差"""
    if not H2H or not H2H.get("common_n"): return ""
    c = H2H["cells"]; n = H2H["common_n"]
    dk, db = H2H["dknow_accurate"], H2H["doubao_accurate"]
    ew = H2H.get("edge_winner", "平手")
    def seg(v, col, lab):
        w = round(v/n*100) if n else 0
        return (f'<div class="h2hseg" style="flex:{max(v,0.001)};background:{col}" '
                f'title="{lab}：{v} 个（{w}%）">{v if v else ""}</div>') if v else ""
    bar = (seg(c["both_accurate"], "#059669", "两家都准确")
           + seg(c["dknow_only"], "#4f46e5", "仅深知晓准确")
           + seg(c["doubao_only"], "#dc2626", "仅豆包准确")
           + seg(c["both_inaccurate"], "#9ca3af", "两家都不准确"))
    return f'''
<div class="h2h">
  <div class="h2htitle"><i class="ti"></i>正面交锋 <span class="muted small">仅在两家<b>共同覆盖的 {n} 个</b>知识点上对比——最公平的直接较量（剔除"谁挑了更容易子集"的偏差）</span></div>
  <div class="h2hbar">{bar}</div>
  <div class="h2hleg">
    <span><i style="background:#059669"></i>两家都准确 {c["both_accurate"]}</span>
    <span><i style="background:#4f46e5"></i>仅深知晓准确 {c["dknow_only"]}</span>
    <span><i style="background:#dc2626"></i>仅豆包准确 {c["doubao_only"]}</span>
    <span><i style="background:#9ca3af"></i>两家都不准确 {c["both_inaccurate"]}</span>
  </div>
  <div class="muted small" style="margin-top:8px">共同覆盖准确数：深知晓 <b style="color:#4f46e5">{dk}</b> ｜ 豆包 <b style="color:#dc2626">{db}</b>（共 {n}）·
     净胜对位：<b>{escape(ew)}</b>{("（仅深知晓答对 "+str(c["dknow_only"])+" 项，反向 "+str(c["doubao_only"])+" 项）") if ew!="平手" else ""}</div>
</div>'''

def prov_html(e, shared_exc=""):
    """各家列：渲染该家【自己引】的『所附依据』。
    去重：若该家所附依据原文与本行『共享官方依据』原文相同（两家常引同一官方文件），
    则不重复整段，只标『同该行官方依据原文』——避免同一段官方原文三连重复。"""
    parts = []
    se = (shared_exc or "").strip()
    binding = {
        "local": "逐段溯源",
        "declared_global": "无对应的清单",
        "answer_level_semantic": "全文语义溯源",
        "none": "未建立溯源",
    }.get(e.get("reference_binding"), e.get("reference_binding") or "未建立溯源")
    for pv in (e.get("provenance_attached") or []):
        ex = pv.get("excerpt","")
        url = pv.get("url","") or pv.get("gov_url","")
        lab = escape(pv.get("label","")[:50])
        link = f'<a href="{_safe_href(url)}" target="_blank">{lab}</a>' if url else lab
        gov = pv.get("gov_url","")
        govlink = f' <a class="govlink" href="{_safe_href(gov)}" target="_blank">[权威核验页]</a>' if gov and gov != url else ''
        platform_url = pv.get("platform_url", "")
        platform_link = (
            f' <a class="govlink" href="{_safe_href(platform_url)}" target="_blank">[深知收录页]</a>'
            if platform_url and platform_url != url else ""
        )
        if ex and se and ex.strip() == se:
            body = '<span class="muted small">（同该行官方依据原文）</span>'
        elif ex:
            body = f'<blockquote>{escape(ex[:200])}</blockquote>'
        else:
            acquisition = pv.get("source_acquisition_status", "")
            if acquisition == "blocked":
                body = '<span class="muted small"> （来源访问受阻，未取得正文）</span>'
            elif acquisition == "failed":
                body = '<span class="muted small"> （来源正文采集失败，未作为正文证据）</span>'
            else:
                body = '<span class="muted small"> （来源正文未取得，未作为正文证据）</span>'
        parts.append(f'<div class="prov"><span class="ptag">所附依据 · {escape(binding)}</span>{link}{govlink}{platform_link}{body}</div>')
    return "".join(parts) or '<span class="muted small">该家未附依据</span>'


def official_basis_html(k):
    """第四步只引用第三步已锁定的权威依据，不再复制整段原文。"""
    ob = k.get("official_basis") or {}
    parts = []
    authority_ref = (
        '<a class="govlink" href="03-authority-report.html">'
        '[查看第三步最终答案与来源索引]</a>'
    )
    res = str(ob.get("verify_result", "") or "")
    gov = ob.get("gov_url", "")
    ex = ob.get("excerpt", "") or ""
    sid = str(ob.get("source_id", "—") or "—")
    if res or ex or (sid not in ("—", "", "None")):
        # 找不到+有链接 的矛盾修正：区分『无任何官方材料』vs『查到管辖件但其中无该明文』
        if res == "通过":
            tag, vtxt = "官方验证 · 通过", "官方源证实"
        elif res == "发现错误":
            tag, vtxt = "官方验证 · 与官方源矛盾", "经核验有误"
        elif res == "找不到" and (gov or ex):
            tag, vtxt = "官方源未见该明文", "已查管辖件，其中未见对应明文表述"
        elif res == "找不到":
            tag, vtxt = "未检索到官方材料", "四通道均无相关官方材料"
        else:
            tag, vtxt = (f"官方验证 · {escape(res)}" if res else "官方依据"), ""
        govlink = f' <a class="govlink" href="{_safe_href(gov)}" target="_blank">[权威核验页]</a>' if gov else ''
        srcid = f'（{escape(sid)}）' if sid not in ("—", "", "None") else ''
        parts.append(f'<div class="prov obasis"><span class="ptag vtag">{tag}</span>'
                     f'<span class="muted small">{vtxt}</span>{srcid}{govlink}{authority_ref}</div>')
    # 官方无明文/待核验：阴性证据（划定边界的原文 + 主文件回链）
    tsk = ob.get("truth_state_K")
    ne = ob.get("null_evidence") or {}
    if tsk in ("官方无明文", "未确定") and (ne.get("silent_clause_excerpt") or ne.get("governing_doc_gov_urls")):
        conf = ob.get("ts_confidence")
        confbadge = f'<span class="confb">{escape(conf)}置信</span>' if conf else ''
        govs = [g for g in (ne.get("governing_doc_gov_urls") or []) if g]
        govlinks = " ".join(f'<a class="govlink" href="{_safe_href(g)}" target="_blank">[主文件{i+1}]</a>'
                            for i, g in enumerate(govs[:3]))
        exn = ne.get("silent_clause_excerpt","")
        ch = ne.get("channels_used")
        chtxt = f'检索通道 {ch} · ' if ch else ''
        label = "官方无明文·阴性证据" if tsk == "官方无明文" else "待核验·检索声明"
        parts.append(f'<div class="prov obasis"><span class="ptag ntag">{label}</span>{confbadge} '
                     f'<span class="muted small">{chtxt}</span>{govlinks}{authority_ref}</div>')
    return "".join(parts)

def _role(k):
    return (k.get("role") or "direct")


def locked_knowledge_point_details(role="direct"):
    """展示第三步 verification.json 已锁定的逐知识点结论，不重新裁决。"""
    blocks = []
    for k in N_list:
        if _role(k) != role:
            continue
        kid = str(k.get("id") or "?")
        finding = escape(str(k.get("authoritative_finding") or "证据不足"))
        platform_cards = []
        for sk, sname, color in SIDE:
            entry = (SE.get(sk) or {}).get(kid) or {}
            category = cat_norm(entry.get("category", "答案遗漏"))
            col, _, category_doc, neutral = CAT_STYLE.get(
                category, CAT_STYLE["答案遗漏"]
            )
            claim = escape(entry.get("claim") or "未覆盖")
            reason = escape(entry.get("verdict_reason") or "该平台未覆盖此知识点。")
            platform_cards.append(
                f'<article class="locked-platform" data-fcx-point="{escape(kid)}" '
                f'data-fcx-platform="{escape(sk)}" '
                f'data-fcx-binding-sha256="{escape(entry.get("semantic_binding_sha256", ""))}">'
                f'<div class="locked-platform-head"><b style="color:{color}">{escape(sname)}</b>'
                f'<span class="catpill" style="background:{col}" title="{escape(category_doc)}">{escape(neutral)}</span></div>'
                f'<p class="locked-claim">{claim}</p>'
                f'<p class="locked-reason"><b>锁定裁决：</b>{reason}</p>'
                f'{prov_html(entry)}'
                '</article>'
            )
        role_label = "直接答案" if role == "direct" else "补充参考"
        blocks.append(
            f'<section class="locked-point" id="evaluation-{escape(kid)}">'
            f'<div class="locked-point-head"><span class="point-id">{escape(kid)}</span>'
            f'<div><span class="role-label">{role_label}</span><h3>{escape(k.get("desc") or "")}</h3></div></div>'
            f'<div class="locked-finding"><b>第三步锁定的权威结论</b><p>{finding}</p>'
            f'{official_basis_html(k)}</div>'
            f'<div class="locked-platforms">{"".join(platform_cards)}</div>'
            '</section>'
        )
    if blocks:
        return "".join(blocks)
    return '<p class="empty-state">本次没有该类知识点。</p>'


def locked_role_section(role="direct"):
    """按知识点角色分区承接第三步锁定结果。"""
    label = "直接答案" if role == "direct" else "补充参考"
    description = (
        "直接回应用户问题；平台表现指标只按本区已有充分证据裁决的知识点计算。"
        if role == "direct"
        else "平台额外提供的信息；单独保留证据价值与风险，不混入最终答案或直接答案指标。"
    )
    return (
        f'<section class="locked-role locked-role-{role}" '
        f'data-fcx-locked-role="{role}">'
        f'<header class="locked-role-head"><div><p class="locked-role-kicker">{label}</p>'
        f'<h3>{label}</h3><p>{description}</p></div></header>'
        f'{locked_knowledge_point_details(role)}'
        '</section>'
    )

# ──────────────── 原始答案 + 参考文献存证 ────────────────
def raw_block(sk, sname, color):
    ans = SCRAPED.get(f"{sk}_answer", "")
    refs = SCRAPED.get(f"{sk}_refs", [])
    badges = [src_badge(r, sk) for r in refs]
    kinds = [source_kind(r, sk) for r in refs]
    n_gov = kinds.count("official_site")
    n_dknow = kinds.count("dknow_trusted_search_official")
    n_com = kinds.count("non_official")
    ref_items = "".join(
        f'<li>{b} <a href="{_safe_href(r.get("url",""))}" target="_blank">'
        f'{escape((r.get("webTitle") or r.get("title") or r.get("url",""))[:70])}</a>'
        + (
            ' <span class="muted small">[已返回来源链接]</span>'
            if r.get("originAttributionStatus") == "trusted_search_official_url"
            else ""
        )
        + (
            f' <a class="govlink" href="{_safe_href(r.get("platformUrl") or r.get("platform_url") or r.get("originalUrl") or r.get("original_url"))}" target="_blank">[深知收录页]</a>'
            if (r.get("platformUrl") or r.get("platform_url") or r.get("originalUrl") or r.get("original_url"))
            and (r.get("platformUrl") or r.get("platform_url") or r.get("originalUrl") or r.get("original_url")) != r.get("url")
            else ""
        )
        + '</li>'
        for r, b in zip(refs, badges))
    stat = f"官方原站 {n_gov} / 官方来源 {n_dknow} / 非官方 {n_com}"
    return f'''
  <details class="rawd">
    <summary><b style="color:{color}">{sname}</b> 原答案（{len(ans)} 字）＋ 参考文献 {len(refs)} 条
      <span class="refstat">{stat}</span></summary>
    <div class="rawans">{escape(ans) if ans else "（未抓到答案）"}</div>
    <ol class="reflist">{ref_items or "<li class='muted'>（无参考文献）</li>"}</ol>
  </details>'''

# ──────────────── 指标口径速查表 ────────────────
def metric_doc_rows():
    return "".join(
        f'<tr><td><b>{escape(name)}</b></td><td>{escape(d[0])}</td><td>{escape(d[1])}</td><td class="muted">{escape(d[2])}</td></tr>'
        for name, d in METRIC_DOC.items())

_ACTIVE_CATS = ("直接准确", "间接准确", "巧合式幻觉", "误导式幻觉", "编造式幻觉", "证据不足", "答案遗漏")
def cat_doc_rows():
    return "".join(
        f'<tr><td><span class="catpill" style="background:{CAT_STYLE[name][0]}">{CAT_STYLE[name][1]} {escape(CAT_STYLE[name][3])}</span></td>'
        f'<td class="muted small">{escape(name)}</td><td>{escape(CAT_STYLE[name][2])}</td></tr>'
        for name in _ACTIVE_CATS)

# ──────────────── 评测元信息 ────────────────
locked_list = [f"{sn}·{kid}" for sk, sn, _ in SIDE
               for kid, e in (SE.get(sk) or {}).items()
               if isinstance(e, dict) and e.get("category_locked")]
result_mtime = datetime.fromtimestamp(Path(a.result).stat().st_mtime).strftime("%Y-%m-%d %H:%M") if Path(a.result).exists() else "—"
meta_rows = f'''
  <tr><td>评测报告生成时间</td><td>{datetime.now().strftime("%Y-%m-%d %H:%M")}</td></tr>
  <tr><td>原始答案抓取数据</td><td>{escape(Path(a.result).name)}（文件时间 {result_mtime}）· 答案与参考文献已在 ④ 全文存证</td></tr>
  <tr><td>分析载体</td><td>当前承载技能的智能体（知识点拆解 / 忠实性 / 官方验证判定）</td></tr>
  <tr><td>自动裁决状态</td><td>{HEALTH.get("total","—")} 个知识点 / 证据不足 {HEALTH.get("evidenceGapCount",0)} 项；证据不足项单列且不按错误计分</td></tr>
  <tr><td>语义分析执行方式</td><td>由当前承载技能的智能体直接完成知识点拆解与证据裁决；脚本不调用任何外部模型接口</td></tr>
  <tr><td>真相源构成</td><td>经严格验收的深知晓官方材料、各平台已附的 gov.cn 材料，或必要时逐知识点调用可信搜索所得官方材料；同一知识点的所有参与方共用同一份验证证据</td></tr>
  <tr><td>可直接判定的官方来源</td><td>官方原站，或由深知可信搜索返回的 dknowc / DT_DATA 官方来源；后者统一标为“官方来源”，可注明“由深知可信搜索收录”</td></tr>
  <tr><td>局限性声明</td><td>网页回答与语义分析均为单次执行结果；可信搜索未取得足够材料时统一标记“证据不足”，并从确定答案和准确率分母中排除</td></tr>'''
if AUTHORITY_VERIFICATION_SHA256:
    meta_rows += (
        '<tr><td>第三步锁定数据摘要</td><td><code>'
        + escape(AUTHORITY_VERIFICATION_SHA256)
        + '</code>；第四步仅展示该锁定数据并计算平台指标，不改变权威结论</td></tr>'
    )

# 申诉声明（中立公信力护栏）：有凭空编造或可申诉项时显示——被评方可提交官方依据复核
_appeal_needed = any(
    (cat_norm(e.get("category","")) == "编造式幻觉" or e.get("appealable"))
    for sk, sn, _ in SIDE
    for e in (SE.get(sk) or {}).values() if isinstance(e, dict)
)
appeal_block = ('''
<div class="appeal">
  <b>关于「凭空编造」判定的声明（申诉通道）</b>
  <p class="small">本平台的「凭空编造」判定，表示在官方原站及官方来源范围内<b>未检索到任何支持该知识点的官方材料</b>。它<b>不构成"该答案绝对错误"的断言</b>——可能官方确有规定而本次检索未覆盖。</p>
  <p class="small">如掌握相关官方一手依据，可通过申诉通道提交，平台将复核、更正并留痕（参照征信业异议流程）。</p>
</div>''' if _appeal_needed else '')

html = f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>各方答案测评报告（问题：{escape(query)}）</title><style>
*{{box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC",sans-serif;width:100%;margin:0;padding:0;color:#1f2937;background:#eef1f5;line-height:1.6;letter-spacing:0}}
.top{{background:#172033;color:#fff;padding:28px max(24px,calc((100vw - 1440px)/2 + 24px))}}
.top .eyebrow{{color:#9bd4ca;font-size:13px;font-weight:700;margin:0 0 8px}}
.top h1{{color:#fff;border:0;font-size:30px;line-height:1.25;margin:0;padding:0}}
.top .muted{{color:#d8e0ec}}
.top .toolbar{{margin-top:14px}}
.top .btn{{background:#fff;border-color:#fff;color:#173f74}}
.report-main{{background:#fff;max-width:1440px;margin:0 auto;padding:24px}}
h1{{color:#1e3a8a;border-bottom:3px solid #e5e7eb;padding-bottom:10px}}
h2{{color:#1e40af;margin-top:34px;border-bottom:1px solid #eee;padding-bottom:6px}}
.muted{{color:#6b7280}} .small{{font-size:12px}}
.tt{{cursor:help;border-bottom:1px dotted #9ca3af}}
.hero{{background:#f6f8fb;border:1px solid #d8dee9;border-left:5px solid #0f766e;padding:20px;border-radius:6px;margin:16px 0}}
.mwrap{{display:grid;grid-template-columns:repeat(var(--platform-count),minmax(0,1fr));gap:16px;margin:14px 0}}
.mcard{{background:#fff;border:1px solid #e1e6ee;border-radius:6px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.07)}}
.mtitle{{font-size:18px;font-weight:700}}
.kpi-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:12px 0 8px}}
.kpi{{min-width:0;border:1px solid #e5e7eb;border-radius:10px;padding:10px;text-align:center;background:#f8fafc}}
.kpi span,.kpi small{{display:block;font-size:11px;color:#64748b}}
.kpi b{{display:block;font-size:25px;line-height:1.2;margin:3px 0}}
.kpi-coverage b{{color:#1e40af}} .kpi-accuracy b{{color:#059669}} .kpi-hallucination b{{color:#dc2626}}
.fabricated-alert{{margin-top:9px;padding:9px 10px;border:2px solid #dc2626;border-radius:8px;background:#fef2f2;color:#991b1b;font-size:13px;font-weight:650}}
.fabricated-clear{{margin-top:9px;padding:7px 9px;border-radius:7px;background:#f0fdf4;color:#15803d;font-size:12px}}
.m2{{font-size:13px;margin:8px 0}}
.mtab{{width:100%;font-size:12.5px;border-collapse:collapse;margin-top:6px}}
.mtab td{{padding:3px 4px;border-bottom:1px solid #f3f4f6}}
.mtab td:last-child{{text-align:right;font-weight:600}}
.mtab tr.hl td{{color:#92400e}}
.kp-scroll{{max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}}
table.kp{{width:100%;border-collapse:collapse;table-layout:fixed;font-size:13px;margin-top:10px}}
.platform-layout-dense table.kp{{min-width:var(--kp-min-width)}}
table.kp th{{background:#f9fafb;padding:8px;text-align:left;border-bottom:2px solid #e5e7eb}}
table.kp td{{padding:8px;border-bottom:1px solid #eee;vertical-align:top}}
.kid{{font-weight:700;color:#6366f1;width:40px}} .kdesc{{width:32%;vertical-align:top}}
.kc{{width:auto;vertical-align:top;overflow-wrap:anywhere}}
.catpill{{display:inline-block;color:#fff;border-radius:5px;padding:2px 8px;font-size:11.5px;font-weight:600;cursor:help}}
.locktag{{display:inline-block;background:#fef3c7;color:#92400e;border:1px solid #fcd34d;border-radius:4px;padding:0 5px;font-size:10.5px;cursor:help}}
.appealtag{{display:inline-block;background:#ede9fe;color:#6d28d9;border:1px solid #c4b5fd;border-radius:4px;padding:0 5px;font-size:10.5px;cursor:help}}
table.ap{{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}}
table.ap th{{background:#f5f3ff;padding:6px 8px;text-align:left;border-bottom:2px solid #ddd6fe;font-size:12px}}
table.ap td{{padding:6px 8px;border-bottom:1px solid #f3f4f6;vertical-align:top}}
.claim{{margin:5px 0;color:#374151}}
.srcline{{font-size:11px;color:#6b7280;margin:3px 0}}
.prov{{margin:4px 0;font-size:11.5px}}
.ptag{{display:inline-block;background:#eef2ff;color:#4338ca;border-radius:3px;padding:1px 5px;margin-right:5px;font-size:10.5px}}
.ptag.vtag{{background:#ecfdf5;color:#047857}}
.ptag.ntag{{background:#ecfeff;color:#0e7490}}
.confb{{display:inline-block;background:#cffafe;color:#0e7490;border-radius:3px;padding:0 5px;font-size:10px;margin-right:4px}}
.govlink{{font-size:10.5px;color:#047857}}
blockquote{{margin:3px 0 3px 8px;padding:3px 8px;border-left:3px solid #d1d5db;background:#fafafa;color:#4b5563;font-size:11.5px}}
.refpill{{display:inline-block;padding:3px 10px;border-radius:999px;color:#fff;font-size:12px;font-weight:600}}
ol.f li{{margin:6px 0}}
details.rawd{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 14px;margin:10px 0}}
details.rawd summary{{cursor:pointer;font-size:14px}}
.refstat{{margin-left:10px;font-size:12px;color:#6b7280;background:#f3f4f6;border-radius:4px;padding:1px 8px}}
.rawans{{white-space:pre-wrap;background:#f9fafb;border-radius:8px;padding:12px;margin:10px 0;font-size:13px;color:#374151;max-height:420px;overflow-y:auto}}
ol.reflist{{font-size:12.5px;margin:6px 0;padding-left:22px}}
ol.reflist li{{margin:3px 0}}
.bgov{{display:inline-block;background:#ecfdf5;color:#047857;border:1px solid #a7f3d0;border-radius:4px;padding:0 5px;font-size:10.5px;margin-right:4px}}
.bdtv{{display:inline-block;background:#ecfeff;color:#0e7490;border:1px solid #a5f3fc;border-radius:4px;padding:0 5px;font-size:10.5px;margin-right:4px;cursor:help}}
.bdt{{display:inline-block;background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:4px;padding:0 5px;font-size:10.5px;margin-right:4px;cursor:help}}
.bcom{{display:inline-block;background:#fff7ed;color:#c2410c;border:1px solid #fed7aa;border-radius:4px;padding:0 5px;font-size:10.5px;margin-right:4px}}
table.doc{{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}}
table.doc th{{background:#f9fafb;padding:6px 8px;text-align:left;border-bottom:2px solid #e5e7eb}}
table.doc td{{padding:6px 8px;border-bottom:1px solid #f3f4f6;vertical-align:top}}
table.meta{{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}}
table.meta td{{padding:6px 8px;border-bottom:1px solid #f3f4f6;vertical-align:top}}
table.meta td:first-child{{width:170px;color:#6b7280;font-weight:600}}
.verdict{{background:#fff;border:1px solid #dbeafe;border-radius:14px;padding:18px 20px;margin:16px 0;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.vq{{font-size:19px;color:#111827;margin-bottom:8px}}
.vqlab{{display:inline-block;background:#1e3a8a;color:#fff;font-size:12px;border-radius:5px;padding:2px 9px;margin-right:9px;vertical-align:middle;font-weight:600}}
.vts{{font-size:14px;margin-bottom:12px}}
.vcards{{display:grid;grid-template-columns:repeat(var(--platform-count),minmax(0,1fr));gap:12px}}
.vcard{{background:#f9fafb;border-radius:8px;padding:10px 14px}}
.vside{{font-size:14px;font-weight:700;margin-bottom:4px}}
.vhead{{font-size:13.5px;color:#374151;line-height:1.55}}
.vbadge{{display:inline-block;color:#fff;font-size:11px;border-radius:4px;padding:1px 7px;margin-right:5px;font-weight:600}}
.tierpill{{display:inline-block;border:1px solid;border-radius:4px;padding:0 5px;font-size:10px;font-weight:600;cursor:help;white-space:nowrap}}
.claimtag{{display:inline-block;background:#eef2ff;color:#4338ca;border-radius:3px;padding:0 5px;font-size:10px;margin-right:5px}}
.evgroup{{border-left:2px solid #e5e7eb;padding-left:8px;margin-top:5px}}
.obgroup{{border-left:3px solid #6ee7b7;padding-left:8px;margin-top:6px;background:#f6fefb;border-radius:0 4px 4px 0;padding:4px 8px}}
.refbox{{border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;background:#fafbfc}}
.refq{{font-weight:600;color:#334155;margin:10px 0 4px;font-size:13.5px}}
.refq:first-child{{margin-top:0}}
.refitem{{font-size:12.5px;color:#475569;padding:4px 0 4px 10px;border-left:2px solid #e2e8f0;margin:3px 0;line-height:1.5}}
.locked-point{{background:#fff;border:1px solid #d7dde7;border-radius:6px;margin:14px 0;padding:18px}}
.locked-point-head{{align-items:flex-start;display:flex;gap:10px}}
.locked-point-head h3{{font-size:17px;line-height:1.5;margin:2px 0 0}}
.point-id{{background:#172033;border-radius:4px;color:#fff;display:inline-block;font-size:12px;font-weight:750;padding:4px 7px}}
.role-label{{color:#637083;font-size:11px;font-weight:700}}
.locked-finding{{background:#f6f8fb;border-left:4px solid #148266;margin:14px 0;padding:12px 14px}}
.locked-finding p{{font-size:15px;font-weight:650;margin:5px 0 6px}}
.locked-finding a{{font-size:12px}}
.locked-platforms{{display:grid;gap:10px;grid-template-columns:repeat(var(--platform-count),minmax(0,1fr))}}
.locked-platform{{background:#fbfcfd;border:1px solid #e1e6ee;border-radius:5px;min-width:0;padding:12px}}
.locked-platform .evgroup{{display:block;width:100%}}
.locked-platform-head{{align-items:center;display:flex;gap:8px;justify-content:space-between}}
.locked-claim{{font-weight:650;margin:9px 0 5px}}
.locked-reason{{color:#526071;font-size:12px;margin:5px 0}}
.empty-state{{background:#f6f8fb;border:1px solid #d7dde7;border-radius:6px;color:#637083;padding:14px}}
.locked-role-stack{{display:grid;gap:24px;margin-top:16px}}
.locked-role{{border-top:6px solid;padding-top:16px}}
.locked-role-direct{{border-top-color:#285a9f}}
.locked-role-reference{{background:#fffaf0;border-top-color:#b7791f;padding:16px 14px 2px}}
.locked-role-head{{align-items:flex-start;display:flex;justify-content:space-between;margin:0 0 12px}}
.locked-role-head h3{{font-size:21px;letter-spacing:0;margin:0}}
.locked-role-head p{{color:#526071;margin:5px 0 0}}
.locked-role-kicker{{font-size:11px!important;font-weight:750;letter-spacing:0!important;margin:0 0 3px!important;text-transform:uppercase}}
.locked-role-direct .locked-role-kicker,.locked-role-direct .locked-role-head h3{{color:#285a9f}}
.locked-role-reference .locked-role-kicker,.locked-role-reference .locked-role-head h3{{color:#8a5712}}
.obasis{{margin:3px 0;font-size:11.5px}}
.ttab{{width:100%;font-size:11.5px;border-collapse:collapse;margin:4px 0 2px}}
.ttab td{{padding:2px 4px;border:none}}
.h2h{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px 18px;margin:14px 0}}
.h2htitle{{font-size:15px;font-weight:600;color:#1e40af;margin-bottom:10px}}
.h2hbar{{display:flex;height:26px;border-radius:6px;overflow:hidden;background:#f1f5f9}}
.h2hseg{{display:flex;align-items:center;justify-content:center;color:#fff;font-size:12px;font-weight:600;min-width:0}}
.h2hleg{{display:flex;flex-wrap:wrap;gap:14px;margin-top:8px;font-size:12px;color:#4b5563}}
.h2hleg i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}}
.appeal{{background:#f0f9ff;border:1px solid #bae6fd;border-radius:10px;padding:12px 16px;margin:16px 0;color:#0c4a6e}}
.appeal p{{margin:5px 0;color:#0c4a6e}}
.toolbar{{display:flex;gap:8px;margin:10px 0 0}}
.btn{{font-size:13px;border:1px solid #c7d2fe;background:#eef2ff;color:#3730a3;border-radius:8px;padding:6px 14px;cursor:pointer}}
.btn:hover{{background:#e0e7ff}}
@media(max-width:1200px){{
  .platform-count-4 .mwrap,.platform-count-4 .vcards{{grid-template-columns:repeat(2,minmax(0,1fr))}}
  .platform-count-5 .mwrap,.platform-count-5 .vcards{{grid-template-columns:repeat(3,minmax(0,1fr))}}
}}
@media(max-width:720px){{
  .mwrap,.vcards,.locked-platforms{{grid-template-columns:1fr!important}}
  table.kp{{min-width:var(--kp-min-width)}}
  body{{overflow-wrap:anywhere}}
  h1{{font-size:24px}}
  .top{{padding:22px 18px}}
  .top h1{{font-size:24px}}
  .report-main{{padding:16px 12px}}
  .locked-role-reference{{padding:14px 10px 2px}}
  .locked-role-head h3{{font-size:19px}}
}}
@media print{{
  body{{max-width:none;margin:0;padding:0;font-size:12px}}
  .toolbar,.tt{{display:none!important}}
  details.rawd{{break-inside:avoid}} details.rawd[open] summary{{font-weight:600}}
  details.rawd:not([open]){{}} .rawans{{max-height:none!important;overflow:visible!important}}
  .verdict,.h2h,.mcard,table.kp tr{{break-inside:avoid}}
  a{{color:#1e40af;text-decoration:none}}
  h1{{font-size:18px}} h2{{font-size:15px}}
}}
</style></head><body class="{PLATFORM_LAYOUT} platform-count-{PLATFORM_COUNT}" style="--platform-count:{PLATFORM_COUNT};--kp-min-width:{KP_MIN_WIDTH}px">
<header class="top">
<p class="eyebrow">Fact-Check-X · 第四阶段</p>
<h1>各方答案测评报告（问题：{escape(query)}）</h1>
<p class="muted">准确性、完整性、来源质量与完整证据 · {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
<div class="toolbar">
  <button class="btn" onclick="window.print()">⎙ 导出 PDF / 打印</button>
  <button class="btn" onclick="document.querySelectorAll('details.rawd').forEach(d=>d.open=true)">▽ 展开全部存证</button>
</div>
</header>
<main class="report-main">

{verdict_block()}

{h2h_block()}

<div class="hero">
  <p style="margin:0 0 6px"><b>结论：</b>{escape(summary)}</p>
  <div class="mwrap">
    {"".join(metric_card(sk, sn, col) for sk, sn, col in SIDE)}
  </div>
  <p class="small muted" style="margin:6px 0 0">指标口径：<b>覆盖率衡量答没答，准确率衡量已有足够证据裁决的回答是否正确</b>。①覆盖率+遗漏率=100%；②准确率+幻觉率=100%，证据不足项不进入二者分母；③证据充分率单独展示当前可裁决覆盖程度；④官方证据支持率与局部角标覆盖率分开呈现；⑤补充参考在同一核验明细中独立分区，不混入直接答案指标。
  各指标含义鼠标悬停可见，完整口径见 <a href="#metricdoc">④ 指标口径速查</a>。</p>
</div>

<h2>① 平台表现概览 <span class="muted small">（仅按直接答案范围计算）</span></h2>
<div class="mwrap">
  {"".join(f'<div class="mcard"><span class="muted small">{escape(sn)}</span><br><span class="refpill" style="background:{col}">{escape(ref.get(sk,"—"))}</span><div class="small muted" style="margin-top:6px">{escape(ref.get(sk+"_note",""))}</div></div>' for sk, sn, col in SIDE)}
</div>

<h2>② 逐知识点核验明细 <span class="muted small">（完整承接第三步锁定结果，不重新核验）</span></h2>
<p class="muted small">本区只引用第三步已经锁定的权威结论，再比较各平台主张与引用忠实性；直接答案和补充参考在同一分析区内明确分开。</p>
<div class="locked-role-stack">
  {locked_role_section("direct")}
  {locked_role_section("reference")}
</div>

<h2>③ 原始答案与参考文献（存证）</h2>
<p class="muted small">评测的原始凭证：各平台 AI 的完整原答案与全部参考文献，未做任何删改。所有知识点判定（②）均可在此回溯核对，包括拆解是否遗漏、平台主张是否忠实于原文。引用旁标注来源类型，并使用当前版本统一口径。</p>
{"".join(raw_block(sk, sn, col) for sk, sn, col in SIDE)}

<h2 id="metricdoc">④ 指标口径速查</h2>
<table class="doc">
  <thead><tr><th style="width:120px">指标</th><th style="width:110px">分母</th><th>定义</th><th style="width:30%">怎么读</th></tr></thead>
  <tbody>{metric_doc_rows()}</tbody>
</table>
<p class="muted small" style="margin-top:10px">知识点判定类别（②中的彩色标签）：外显使用描述性中性文案，标准分类可在鼠标悬停标签时查看。</p>
<table class="doc">
  <thead><tr><th style="width:150px">外显标签</th><th style="width:96px">标准分类</th><th>判定条件</th></tr></thead>
  <tbody>{cat_doc_rows()}</tbody>
</table>

{("<h2>④-补 申诉记录（复核更正留痕）</h2>" +
  "<p class='muted small'>下列结论经申诉复核更正，对应知识点带「⚖ 经申诉更正」标记。更正可双向（推翻错罚或错奖），全程留痕。</p>" +
  "<table class='ap'><thead><tr><th>申诉号</th><th>对象</th><th>更正</th><th>提交方</th><th>复核</th><th>依据/理由</th></tr></thead><tbody>" +
  "".join(
    f"<tr><td>{escape(str(x.get('appeal_id','')))}</td><td>{escape(x.get('side',''))}·{escape(x.get('kid',''))}</td>"
    f"<td>{escape(x.get('prev_category',''))} → <b>{escape(x.get('new_category',''))}</b></td>"
    f"<td class='small'>{escape(x.get('submitter',''))}</td>"
    f"<td class='small'>{escape(x.get('reviewer',''))} {escape(x.get('reviewed_at',''))}</td>"
    f"<td class='small'>" + (f"<a href='{_safe_href(x.get('evidence_url',''))}' target='_blank'>依据</a> " if x.get('evidence_url') else "") +
    f"{escape(x.get('rationale','')[:70])}</td></tr>"
    for x in APPEALS) +
  "</tbody></table>") if APPEALS else ""}

<h2>⑤ 评测元信息（可复现性）</h2>
<table class="meta"><tbody>{meta_rows}</tbody></table>
{appeal_block}

<p class="muted small" style="margin-top:36px;border-top:1px solid #eee;padding-top:12px">
Fact-Check-X · 评测口径：覆盖率衡量平台是否回答直接知识点；准确率与幻觉率仅以已有足够证据裁决的覆盖项为分母；证据不足项单独展示，不按正确或错误计分；全判定可回溯至平台原答案、所附依据和权威证据。</p>
</main></body></html>'''

ts = datetime.now().strftime("%Y%m%d_%H%M")
out = Path(a.out) if a.out else (Path(__file__).parent / "reports" / f"factcheck_{ts}_{a.topic}.html")
out.parent.mkdir(exist_ok=True, parents=True)
out.write_text(html, encoding="utf-8")
print(f"各方答案测评报告: {out}")
print(f"   大小: {len(html):,} 字符")
for sk, sn, _ in SIDE:
    m = M.get(sk, {})
    print(f"   {sn}: 覆盖 {m.get('覆盖率',0)*100:.0f}% · 准确 {m.get('准确率',0)*100:.0f}% · 幻觉 {m.get('幻觉率',0)*100:.0f}%")
if not a.no_open:
    subprocess.run(["open", str(out)])
