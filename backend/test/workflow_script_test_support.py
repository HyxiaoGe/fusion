import sys
from pathlib import Path

MONOREPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_HELPERS = MONOREPO_ROOT / ".github/scripts"
if str(SCRIPT_HELPERS) not in sys.path:
    sys.path.insert(0, str(SCRIPT_HELPERS))

from deploy_workflow_test_support import expand_deploy_scripts  # noqa: E402


def read_expanded_workflow(path: Path) -> str:
    return expand_deploy_scripts(path.read_text(encoding="utf-8"), MONOREPO_ROOT)
