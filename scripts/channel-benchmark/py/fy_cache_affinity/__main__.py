"""Allow `python -m fy_cache_affinity` invocation."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
