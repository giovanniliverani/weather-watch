#!/usr/bin/env python3
"""Emit SQL to find listed column names missing from a live Unity Catalog table.

Use when you have a name list (JSON or --name flags) and need to check it
against Databricks. Prints SQL only — run it via SQL MCP or `task query`.

Do not use when you already have DESCRIBE / information_schema output and can
diff in the shell, or when the list belongs inside a job skill that already
loads live schemas (e.g. the ECC mapping generator).

Placeholders (scripts use flags, not square brackets in code):

    python validate-column-list.py --table [catalog].[schema].[table] --name [col_a] --name [col_b]
    python validate-column-list.py --table [catalog].[schema].[table] --json [path] --model [ecc_model_key]

Filled example (gross profit high-priority list vs main.gold):

    python validate-column-list.py --table main.gold.fct_gross_profit \\
      --json databricks/.claude/skills/ecc-s4-migration-mapping/references/high_priority_columns.json \\
      --model fct_gross_profit
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def split_table(table: str) -> tuple[str, str, str]:
    parts = table.split(".")
    if len(parts) != 3 or not all(parts):
        raise SystemExit("ERROR: --table must be catalog.schema.table")
    return parts[0], parts[1], parts[2]


def names_from_json(path: Path, model: str | None) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if model:
        spec = (payload.get("models") or {}).get(model)
        if spec is None:
            raise SystemExit(f"ERROR: model {model!r} not in {path}")
        columns = spec.get("columns") or []
        return [str(c.get("name") or "").strip() for c in columns if c.get("name")]
    if isinstance(payload, list):
        return [str(n).strip() for n in payload if str(n).strip()]
    columns = payload.get("columns") or []
    if columns and isinstance(columns[0], dict):
        return [str(c.get("name") or "").strip() for c in columns if c.get("name")]
    return [str(n).strip() for n in columns if str(n).strip()]


def emit_sql(catalog: str, schema: str, table: str, names: list[str]) -> str:
    unique = list(dict.fromkeys(n for n in names if n))
    if not unique:
        raise SystemExit("ERROR: no column names to check")
    literals = ", ".join("'" + n.replace("'", "''") + "'" for n in unique)
    return f"""WITH wanted(column_name) AS (
  SELECT explode(array({literals}))
)
SELECT w.column_name AS missing
FROM wanted w
LEFT JOIN `{catalog}`.information_schema.columns c
  ON c.table_schema = '{schema}'
 AND c.table_name = '{table}'
 AND c.column_name = w.column_name
WHERE c.column_name IS NULL
ORDER BY 1;
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table", required=True, help="catalog.schema.table")
    ap.add_argument("--json", type=Path, help="JSON with columns or models.<key>.columns")
    ap.add_argument("--model", help="Key under JSON 'models' (e.g. fct_gross_profit)")
    ap.add_argument("--name", action="append", default=[], help="Repeat for each column")
    args = ap.parse_args()
    names = list(args.name)
    if args.json:
        names.extend(names_from_json(args.json, args.model))
    catalog, schema, table = split_table(args.table)
    sys.stdout.write(emit_sql(catalog, schema, table, names))


if __name__ == "__main__":
    main()
