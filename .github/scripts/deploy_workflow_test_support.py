import re
from pathlib import Path


BODY_MARKER = "# OPS_DEPLOY_BODY_BEGIN\n"
SCRIPT_RUN_PATTERN = re.compile(r"^(?P<indent>\s*)run: (?P<path>ops/deploy/[^\s]+\.sh)\s*$")


def deploy_script_body(root: Path, script_path: str) -> str:
    content = (root / script_path).read_text(encoding="utf-8")
    if BODY_MARKER not in content:
        raise AssertionError(f"部署脚本缺少原始 run 块标记: {script_path}")
    return content.split(BODY_MARKER, 1)[1]


def expand_deploy_scripts(workflow: str, root: Path) -> str:
    expanded: list[str] = []
    for line in workflow.splitlines():
        match = SCRIPT_RUN_PATTERN.match(line)
        if match is None:
            expanded.append(line)
            continue
        indent = match.group("indent")
        body = deploy_script_body(root, match.group("path")).rstrip("\n")
        expanded.append(f"{indent}run: |")
        expanded.extend(f"{indent}  {body_line}" for body_line in body.splitlines())
    return "\n".join(expanded) + "\n"
