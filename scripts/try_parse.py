"""Read a document with the local model and print the result (for trying models and prompts).

Usage: OLLAMA_URL=http://localhost:11434 .venv/bin/python scripts/try_parse.py <file> [receipt|statement|auto]
"""
import json
import mimetypes
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("BUDGET_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "budget_tracker"))

from app.ai_parser import parse_document  # noqa: E402
from app.seed import DEFAULT_CATEGORIES  # noqa: E402

path = Path(sys.argv[1])
kind = sys.argv[2] if len(sys.argv) > 2 else "auto"
mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
cats = [c[0] for c in DEFAULT_CATEGORIES if c[1] == "expense"]
t = time.time()
result = parse_document(path, mime, kind, cats)
print(json.dumps(result, ensure_ascii=False, indent=2))
print(f"\nTook {time.time() - t:.1f} s", file=sys.stderr)
