#!/usr/bin/env python3
"""prepare-runtime 必须回报当前实际运行的技能包版本。

载体界面显示的可能是应用市场上的可用版本而不是本机已装版本（2026-09-19 实际发生过：
界面显示 1.1.17，磁盘上是 1.1.16，两次安装都没落地却看不出来）。版本必须取自正在
执行的这份脚本所在目录，不依赖载体的安装记录。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_module():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("fcx", ROOT / "scripts" / "fact_check_x.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()

    declared = json.loads((ROOT / "package-manifest.json").read_text(encoding="utf-8"))["version"]
    reported = module.installed_version()
    assert reported["version"] == declared, f"版本应取自包内清单：清单 {declared}，回报 {reported['version']}"
    assert reported["source"].endswith("package-manifest.json"), reported["source"]
    assert Path(reported["source"]).is_file(), "来源路径必须真实存在，便于排查装到了哪一份"

    # 清单缺失时退回 SKILL.md，仍不得回报 unknown
    with tempfile.TemporaryDirectory(prefix="fcx-version-") as raw:
        fake = Path(raw) / "fact-check-x-complete"
        fake.mkdir(parents=True)
        shutil.copytree(ROOT / "scripts", fake / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "SKILL.md", fake / "SKILL.md")
        # 故意不放 package-manifest.json，验证退回 SKILL.md 的路径
        probe = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util,json,sys;"
             f"sys.path.insert(0, r'{fake}/scripts');"
             f"spec=importlib.util.spec_from_file_location('m',r'{fake}/scripts/fact_check_x.py');"
             "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
             "print(json.dumps(m.installed_version()))"],
            text=True, capture_output=True, check=False,
        )
        assert probe.returncode == 0, probe.stderr
        fallback = json.loads(probe.stdout)
        assert fallback["version"] == declared, f"缺清单时应退回 SKILL.md：{fallback}"
        assert fallback["source"].endswith("SKILL.md"), fallback["source"]

    # prepare-runtime 的返回里必须带上它
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fact_check_x.py"), "prepare-runtime"],
        text=True, capture_output=True, check=False, timeout=600,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload.get("installedVersion", {}).get("version") == declared, payload.get("installedVersion")

    print("PASS prepare-runtime 回报真实安装版本，清单缺失时退回 SKILL.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
