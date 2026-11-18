"""Entry point: uv run --env-file .env generate.py --month 2026-10 [--bad-files 2]"""

from savehub.generator import main

if __name__ == "__main__":
    main()
