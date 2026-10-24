"""Run from the repository after installation: python scripts/generate_demo.py --import."""
import sys
from app.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["generate-demo", *sys.argv[1:]]))
