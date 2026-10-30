"""Build tools.json, the snapotter CLI's tool catalogue, from apps/api/src/openapi.yaml.

Usage (repo root):  python3 tools/snapotter-cli/build_catalog.py
Needs PyYAML (build time only; the CLI itself is standard-library only).

Each tool route (POST /api/v1/tools/{section}/{tool}) becomes one entry:
  id, section, name, summary, path, inputs (multipart file fields), settings.
Settings come from the `settings` field's description, which the spec writes
as one bullet per option:
  - `quality` (string, default "balanced") - One of: light, balanced, strong
Bullets that do not follow that shape are kept as raw text and the tool is
listed in `partial`, so `describe` can show them and `run` can still pass
them through unvalidated. A settings description written as prose (most of
the dedicated "X to Y" converters) is kept whole as `settingsText`.

Options that are their own form fields (multipart) or JSON body properties
(the generators, which take no upload) become `fields`, with type, default,
choices and limits from the schema. `body` says how the request is sent:
multipart, json, or none (a guidance-only route).
"""
import json
import os
import re
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SPEC = os.path.join(ROOT, "apps", "api", "src", "openapi.yaml")
OUT = os.path.join(os.path.dirname(__file__), "tools.json")

PATH_RE = re.compile(r"^/api/v1/tools/([a-z]+)/([a-z0-9-]+)$")
BULLET_RE = re.compile(r"^\s*-\s+`([A-Za-z0-9_.\[\]]+)`\s*\(([^)]*)\)\s*(?:-\s*(.*))?$")
CHOICES_RE = re.compile(r"One of:\s*([^.;]+?)(?:[.;]|$)")


def parse_meta(meta):
    """'string, default "balanced"' -> ('string', 'balanced', False)."""
    parts = [p.strip() for p in meta.split(",")]
    typ = parts[0] if parts else ""
    default, required = None, False
    for p in parts[1:]:
        if p.startswith("default"):
            raw = p[len("default"):].strip()
            if raw.startswith('"') and raw.endswith('"'):
                default = raw[1:-1]
            else:
                try:
                    default = json.loads(raw)
                except ValueError:
                    default = raw
        elif p == "required":
            required = True
    return typ, default, required


def parse_settings(text):
    settings, raw = [], []
    for line in (text or "").splitlines():
        if not line.strip() or line.strip().lower().startswith("json string"):
            continue
        m = BULLET_RE.match(line)
        if not m:
            if line.lstrip().startswith("-"):
                raw.append(line.strip())
            elif settings:  # continuation of the previous bullet
                settings[-1]["description"] += " " + line.strip()
            continue
        name, meta, desc = m.group(1), m.group(2), (m.group(3) or "").strip()
        typ, default, required = parse_meta(meta)
        entry = {"name": name, "type": typ, "description": desc}
        if default is not None:
            entry["default"] = default
        if required:
            entry["required"] = True
        cm = CHOICES_RE.search(desc)
        if cm:
            entry["choices"] = [c.strip() for c in cm.group(1).split(",") if c.strip()]
        settings.append(entry)
    return settings, raw


def schema_field(name, prop, required):
    entry = {"name": name, "type": prop.get("type", ""), "description": " ".join((prop.get("description") or "").split())}
    for key in ("default", "minimum", "maximum"):
        if key in prop:
            entry[key] = prop[key]
    if "enum" in prop:
        entry["choices"] = [str(v) for v in prop["enum"]]
    if name in required:
        entry["required"] = True
    return entry


def main():
    spec = yaml.safe_load(open(SPEC, encoding="utf-8"))
    tools, partial = [], []
    for path, item in sorted(spec.get("paths", {}).items()):
        m = PATH_RE.match(path)
        if not m or "post" not in item:
            continue
        section, tool_id = m.groups()
        op = item["post"]
        content = (op.get("requestBody") or {}).get("content") or {}
        body = "multipart" if "multipart/form-data" in content else "json" if "application/json" in content else "none"
        schema = (content.get("multipart/form-data") or content.get("application/json") or {}).get("schema") or {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        inputs, settings, raw, fields, settings_text = [], [], [], [], None
        for field, prop in props.items():
            if field == "clientJobId":
                continue
            if field == "settings" and body == "multipart":
                text = prop.get("description", "")
                settings, raw = parse_settings(text)
                if not settings and not raw and text.strip():
                    settings_text = " ".join(text.split())
                continue
            is_binary = prop.get("format") == "binary"
            is_array = prop.get("type") == "array" and (prop.get("items") or {}).get("format") == "binary"
            if is_binary or is_array:
                inputs.append({"field": field, "multiple": is_array, "required": field in required,
                               "description": (prop.get("description") or "").strip()})
            else:
                fields.append(schema_field(field, prop, required))
        entry = {
            "id": tool_id,
            "section": section,
            "name": op.get("summary") or tool_id,
            "summary": " ".join((op.get("description") or "").split())[:300],
            "path": path,
            "body": body,
            "inputs": inputs,
            "settings": settings,
        }
        if settings_text:
            entry["settingsText"] = settings_text
        if fields:
            entry["fields"] = fields
        if raw:
            entry["unparsed"] = raw
            partial.append(tool_id)
        tools.append(entry)
    out = {
        "generatedFrom": "apps/api/src/openapi.yaml",
        "apiVersion": (spec.get("info") or {}).get("version"),
        "count": len(tools),
        "partial": partial,
        "tools": tools,
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"{len(tools)} tools -> {os.path.relpath(OUT, ROOT)} ({os.path.getsize(OUT) // 1024} KB); "
          f"{len(partial)} with unparsed settings lines")
    return 0 if tools else 1


if __name__ == "__main__":
    sys.exit(main())
