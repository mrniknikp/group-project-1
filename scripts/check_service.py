"""Тонкая обёртка conformance-CLI (``check-service``).

Логика живёт в ``contract/check_service.py``; модуль нужен, чтобы проверку
можно было запускать как ``python3 scripts/check_service.py`` без установки
пакета и чтобы entry point ``check-service`` оставался стабильным.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract.check_service import main  # noqa: E402

__all__ = ['main']

if __name__ == '__main__':
    sys.exit(main())
