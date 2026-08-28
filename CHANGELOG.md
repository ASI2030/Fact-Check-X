# Changelog

All notable changes are documented in this file. The project follows Semantic Versioning.

## [Unreleased]

## [1.1.6] - 2026-08-28

- Fixed deep-trace citation binding so displayed markers such as `【6】` resolve to their actual source and compound claims can combine locally bound evidence without borrowing unrelated references.
- Added mandatory user-visible checkpoints after all four stages and a consistent navigation bar across the four standalone HTML reports.
- Replaced ambiguous stage-four summary labels with explicit direct-official, independently verified nonofficial, and uncited coincidental-agreement wording.
- Removed the user-facing `来源链接待补` placeholder while retaining missing-origin provenance in structured metadata.
- Added regression coverage for the reported Shenzhen K5 citation pattern, all-stage delivery, report navigation, and the revised evaluation wording.

## [1.1.5] - 2026-08-19

- Fixed the authority verifier so 深知晓（深度溯源） can independently use its own qualified official materials as a zero-request trusted anchor, without inheriting another platform's trust state.
- Made authority finalization transactional: result merging and report rendering must finish before the gate becomes `finalized`; failures now restore `searched`, remove transient artifacts and allow a direct retry.
- 修复权威核验报告在手机窄屏下被长链接撑宽的问题，证据卡片与原文摘录现在会在卡片内安全换行。

- Preserved DeepKnown citation superscripts before text extraction, excluded time values such as `20:30` and `21:00` from bare citation markers, and removed source-card prose from the captured main answer.
- Renamed the optional platform to “深知晓（深度溯源）” and made its captured official materials independently eligible for direct verification without inheriting another platform's trust status.
- Renamed and polished the four user-facing reports, separated verified conclusions from unresolved claims, and kept stage-four evaluation bound to stage-three verification data.
- Added an idempotent `prepare-runtime` bootstrap that installs the locked Node.js dependencies on a clean machine, skips browser downloads, and verifies the collector before use instead of relying on stale `node_modules`.
- Counted Doubao sources across both inline answer links and the expanded source list, including overlap, while retaining confirmed answer text and partial references when completeness checks fail.
- Recognized Qianwen's slider-verification wording as a human-verification gate instead of waiting until answer extraction times out.

## [1.1.4] - 2026-08-14

- Unified 深知晓 and 深知晓（深度研究）trusted-search materials under the public label “官方来源” without requiring a `.gov` domain or an external source URL.
- Preserved optional source trace links while preventing answer-page context from being treated as linked source text or supporting evidence.
- Improved authority-report readability by showing the most relevant evidence excerpt first and folding the full source text, while keeping stage 4 bound to the locked stage-3 verification data.
- Updated browser regressions to use an installed system Chromium browser instead of depending on Chrome for Testing.

## [1.1.3] - 2026-08-12

- Re-centered the public presentation on the complete fact-checking workflow while retaining authoritative answer generation as the third-stage capability.
- Added a high-information-density poster covering the six supported platforms, four independent deliverables, evidence boundaries, API Key rule and current SkillHub installation command.
- Aligned the GitHub and SkillHub descriptions with the rule that only evidence-supported claims enter the final answer.

## [1.1.2] - 2026-08-12

- Added a product overview image to the GitHub README and SkillHub description.
- Aligned the Chinese and English product introductions with the evidence-gap behavior introduced in v1.1.1.
- Corrected the documented SkillHub installation command to use the namespace flag.

## [1.1.1] - 2026-08-11

- Separated Trusted Search technical failures from evidence insufficiency: service failures now retry up to three times and remain a technical stage error if exhausted.
- Made `no_evidence` and unresolved claims deliverable as explicit evidence gaps, without requiring human review or blocking the fourth-stage report.
- Excluded evidence-insufficient claims from deterministic final answers and accuracy denominators while retaining them in the evidence-boundary reports.

## [1.1.0] - 2026-07-30

- Reframed the public product around four user-facing deliverables: original answers and citations, unverified knowledge comparison, authoritative evidence verification, and platform performance with complete evidence.
- Added market-first SkillHub metadata, a clear first-run experience, optional Trusted Search onboarding, and an explicit six-platform support matrix.
- Limited the formal support commitment to 深知晓, 深知晓（深度溯源）, 豆包, 腾讯元宝, DeepSeek and 通义千问; removed unverified generic adapters from the built-in platform list.
- Removed internal revision labels and carrier-specific acceptance links from public descriptions and generated reports.
- Renamed final report scripts and visible report labels to stable product terms.
- Added a deterministic SkillHub package builder that rejects relative documentation links, internal terminology and extensionless legal files.
- Added regression coverage for SkillHub presentation, supported-platform truth and market-package reproducibility.

## [1.0.0] - 2026-07-30

- Published the first public source release of 全知晓（Fact-Check-X）.
- Added Apache-2.0 licensing, third-party notices, privacy and security policies.
- Added reproducible release building, installation, public-boundary auditing and SHA256 verification tools.
- Preserved dynamic platform semantics for any selected platform count `N ≥ 1`.
- Preserved separate `dknowc-chat` and `dknowc-deep-research` semantics.
- Bound the formal complete package to SHA256 `007fe204cddff50a19ecfd1d82e3c0c52c21ef3ff4ee73a45b4e99f5303165b6`.
- Increased deterministic timeout budgets and added bounded timeout retries for the golden regression gate and browser capture waits.
