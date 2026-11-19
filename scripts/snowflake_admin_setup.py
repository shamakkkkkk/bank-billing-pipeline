"""Run the one-time Snowflake setup (secrets/snowflake_setup.sql) as the account admin.

The admin password is typed into a hidden prompt and used only for this one
connection: it is never stored, logged or written to a file. Afterwards the
pipeline uses its own service user with the key pair, not this password.

    uv run --env-file .env python scripts/snowflake_admin_setup.py
"""

import getpass
import os
import pathlib
import subprocess
import sys

import snowflake.connector

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETUP = ROOT / "secrets" / "snowflake_setup.sql"


def statements(sql: str) -> list[str]:
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def main() -> None:
    account = os.environ.get("SNOWFLAKE_ACCOUNT")
    if not account:
        sys.exit("SNOWFLAKE_ACCOUNT is empty in .env")
    if not SETUP.exists():
        subprocess.run([str(ROOT / "scripts" / "snowflake_setup.sh")], check=True)

    print(f"Snowflake account {account}: log in with the admin user you created at sign-up.")
    user = input("Admin username: ").strip()
    password = getpass.getpass("Admin password (hidden): ")
    params = {"account": account, "user": user, "password": password}
    passcode = getpass.getpass("MFA code from your authenticator app (Enter if you have no MFA): ")
    if passcode.strip():
        params |= {"authenticator": "USERNAME_PASSWORD_MFA", "passcode": passcode.strip()}

    con = snowflake.connector.connect(**params)
    try:
        with con.cursor() as cur:
            for stmt in statements(SETUP.read_text()):
                first_line = stmt.splitlines()[0][:70]
                cur.execute(stmt)
                print(f"  ok  {first_line}")
            rows = cur.fetchall()
            names = [d[0] for d in cur.description]
            for row in rows:
                record = dict(zip(names, row, strict=False))
                if record.get("property") == "RSA_PUBLIC_KEY_FP":
                    print(f"\nRSA_PUBLIC_KEY_FP = {record.get('value')}")
    finally:
        con.close()
    print("\nDone. The pipeline can now log in as SAVEHUB_PIPELINE with the key pair.")


if __name__ == "__main__":
    main()
