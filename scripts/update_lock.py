"""Record the installed environment without embedding the editable checkout path."""
from pathlib import Path
import subprocess
import sys

lines = subprocess.check_output([sys.executable, "-m", "pip", "freeze", "--all"], text=True).splitlines()
lines = [line for line in lines if not line.startswith(("-e ", "# Editable"))]
lines = [line + '; sys_platform == "win32"' if line.startswith("colorama==") else line for line in lines]
(Path(__file__).resolve().parents[1] / "requirements.lock").write_text(
    "# Verified Windows x64 / Python 3.12. Runtime, test and build dependencies.\n" + "\n".join(lines) + "\n", encoding="utf-8")
