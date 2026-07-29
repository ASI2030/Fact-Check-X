# Contributing

Thank you for improving 全知晓（Fact-Check-X）.

## Before opening a change

1. Search existing issues and pull requests.
2. Use synthetic examples. Never submit a real session, Cookie, API Key, customer question, private report or local user path.
3. Keep each change within one reviewable behavior or release boundary.
4. Preserve the four-stage product contract and fail-closed gates.
5. Preserve `N ≥ 1` dynamic platform behavior; do not introduce a fixed platform count.
6. Keep `dknowc-chat` and `dknowc-deep-research` as separate platform semantics.

## Source layout

- `skills/fact-check-x-complete` is the WorkBuddy-facing orchestrator source.
- The other directories under `skills/` are independently installable modules.
- The release builder composes the complete package from the orchestrator and the three business modules. Do not commit generated ZIP files.

## Required checks

```bash
python3 scripts/audit_public_tree.py
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/build_release.py --out-dir <temporary-output> --version 0.0.0-dev
python3 scripts/verify_release.py manifest \
  --manifest <temporary-output>/release-manifest.json \
  --asset-dir <temporary-output>
```

Run the affected skill's smoke tests as well. Browser-dependent tests must use test accounts and must not commit profiles or results.

## Pull requests

Describe:

- the observable problem;
- the behavior before and after the change;
- the tests and evidence;
- security, privacy and compatibility impact;
- whether release notes are required.

By intentionally submitting a contribution, you agree that it is provided under Apache-2.0 unless the file clearly carries a compatible third-party license.
