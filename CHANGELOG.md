# Changelog

All notable changes are documented in this file. The project follows Semantic Versioning.

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
- Limited the formal support commitment to 深知晓, 深知晓（深度研究）, 豆包, 腾讯元宝, DeepSeek and 通义千问; removed unverified generic adapters from the built-in platform list.
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
