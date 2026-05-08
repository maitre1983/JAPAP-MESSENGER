"""
Extract legacy JAPAP user records (user_id, username, email, password, first_name,
last_name) from a phpMyAdmin MySQL dump of the `Wo_Users` table.

We don't need a full SQL parser — we only care about the first 6 columns of each
VALUES tuple. Runs in-memory against tuples streamed from a single pass over the
file. Output: /app/legacy_migration/users.jsonl (one JSON object per line).
"""
import json
import re
import sys
from pathlib import Path

SRC = Path("/app/legacy_migration/japap_users.sql")
OUT = Path("/app/legacy_migration/users.jsonl")

# Grab every INSERT INTO `Wo_Users` … VALUES block body
_INSERT_RE = re.compile(
    r"INSERT INTO `Wo_Users`[^;]+?VALUES\s*(.*?);\s*(?=\n)",
    re.DOTALL,
)


def parse_values_body(body: str):
    """Yield a list of the first 6 field values from each VALUES tuple.

    Handles:
      • single-quoted strings with \\' and \\\\ escape sequences
      • NULL (unquoted)
      • integers / bare tokens
    """
    i = 0
    n = len(body)
    while i < n:
        # Skip whitespace + commas between tuples
        while i < n and body[i] in " \r\n\t,":
            i += 1
        if i >= n:
            return
        if body[i] != "(":
            # Defensive: skip anything unexpected
            i += 1
            continue
        i += 1  # consume (
        fields = []
        depth_ok = True
        while i < n:
            # Skip whitespace
            while i < n and body[i] in " \r\n\t":
                i += 1
            if i >= n:
                depth_ok = False
                break
            ch = body[i]
            if ch == ")":
                i += 1
                break
            if ch == "'":
                # Quoted string
                i += 1
                start = i
                buf = []
                while i < n:
                    c = body[i]
                    if c == "\\" and i + 1 < n:
                        nxt = body[i + 1]
                        if nxt in ("'", '"', "\\"):
                            buf.append(nxt)
                            i += 2
                            continue
                        if nxt == "n":
                            buf.append("\n")
                            i += 2
                            continue
                        if nxt == "r":
                            buf.append("\r")
                            i += 2
                            continue
                        if nxt == "t":
                            buf.append("\t")
                            i += 2
                            continue
                        if nxt == "0":
                            buf.append("\x00")
                            i += 2
                            continue
                        # Unknown escape — keep next char as-is
                        buf.append(nxt)
                        i += 2
                        continue
                    if c == "'":
                        i += 1
                        break
                    buf.append(c)
                    i += 1
                fields.append("".join(buf))
            else:
                # Bare token until , or )
                start = i
                while i < n and body[i] not in ",)":
                    i += 1
                token = body[start:i].strip()
                if token.upper() == "NULL":
                    fields.append(None)
                else:
                    fields.append(token)
            # After a field, skip optional whitespace/comma
            while i < n and body[i] in " \r\n\t":
                i += 1
            if i < n and body[i] == ",":
                i += 1
        if depth_ok:
            yield fields[:6]  # user_id, username, email, password, first_name, last_name


def main():
    text = SRC.read_text(encoding="utf-8", errors="replace")
    total = 0
    written = 0
    seen_emails: set[str] = set()
    with OUT.open("w", encoding="utf-8") as out:
        for match in _INSERT_RE.finditer(text):
            body = match.group(1)
            for fields in parse_values_body(body):
                total += 1
                if len(fields) < 3:
                    continue
                uid, username, email, pw, fname, lname = (
                    fields + [None] * (6 - len(fields))
                )[:6]
                email = (email or "").strip().lower()
                if not email or "@" not in email:
                    continue
                if email in seen_emails:
                    continue
                seen_emails.add(email)
                rec = {
                    "legacy_user_id": int(uid) if uid and str(uid).isdigit() else None,
                    "username": (username or "").strip(),
                    "email": email,
                    "first_name": (fname or "").strip(),
                    "last_name": (lname or "").strip(),
                }
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
    print(f"Parsed {total} VALUES tuples, wrote {written} unique users to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
