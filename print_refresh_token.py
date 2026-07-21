"""Local-only dev utility: prints the Google refresh_token stored in
investigator.db so you can copy it into GitHub Actions repo secrets
(Settings -> Secrets and variables -> Actions -> GOOGLE_REFRESH_TOKEN).

Never run this in CI - it just reads a local file that isn't committed.
Run /login/google in a browser first if this comes up empty.
"""
import sqlite3
import sys

conn = sqlite3.connect("investigator.db")
cursor = conn.cursor()
cursor.execute("SELECT refresh_token FROM auth_tokens WHERE provider = 'google'")
row = cursor.fetchone()
conn.close()

if not row or not row[0]:
    print("No refresh_token stored yet. Visit /login/google, complete the consent screen, and try again.", file=sys.stderr)
    sys.exit(1)

print(row[0])
