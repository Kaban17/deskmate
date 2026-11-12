import os
from pathlib import Path

PROMPTS_DIR = Path(os.environ.get("PROMPTS_DIR") or Path(__file__).resolve().parents[2] / "prompts")


def load_prompt(version: str, root: Path = PROMPTS_DIR) -> str:
    """Return the system prompt for `version`; raises FileNotFoundError if unknown."""
    return (root / "system" / f"{version}.md").read_text(encoding="utf-8").strip()
