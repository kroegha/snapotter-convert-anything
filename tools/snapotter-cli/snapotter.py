#!/usr/bin/env python3
"""snapotter: run SnapOtter tools on local files from the command line.

Talks to the local SnapOtter API (default http://127.0.0.1:1349) with an API
key read from a file. Standard library only.

  snapotter tools [--section video] [--search subtitles]
  snapotter describe compress-video
  snapotter run compress-video clip.mp4 --set quality=strong
  snapotter run merge-pdf a.pdf b.pdf --out merged.pdf
  snapotter run remove-background --batch photos/*.jpg
  snapotter run qr-generate --set text=hello --out qr.png
  snapotter pipeline steps.json photo.jpg
  snapotter run transcribe-audio long.m4a --detach      (prints the job id)
  snapotter wait <jobId> --out C:\\Users\\cfish\\Videos
  snapotter status

Results are written next to the input (or to --out) and never overwrite an
existing file unless --force is given. --json prints one JSON object per
result on stdout. Exit codes: 0 ok, 1 unexpected error, 2 bad arguments or
settings, 3 rejected by the server, 4 the job failed, 5 cannot reach the server,
6 still running when --timeout ran out (run `snapotter wait <jobId>` again).

Key file: %USERPROFILE%\\.config\\snapotter\\key (Windows) or
~/.config/snapotter/key, or the path in SNAPOTTER_KEY_FILE. The key is never
printed, logged or accepted on the command line.
"""
import argparse
import difflib
import glob
import http.client
import ipaddress
import json
import mimetypes
import os
import re
import sys
import tempfile
import time
import urllib.parse
import uuid

VERSION = "1.1.0"
DEFAULT_BASE = "http://127.0.0.1:1349"
CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools.json")
CHUNK = 1 << 20

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_REJECTED, EXIT_FAILED, EXIT_UNREACHABLE, EXIT_PENDING = 0, 1, 2, 3, 4, 5, 6


class CliError(Exception):
    def __init__(self, message, code=EXIT_ERROR):
        super().__init__(message)
        self.code = code


# ── catalogue ────────────────────────────────────────────────────────────────


def load_catalog(path=CATALOG_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise CliError(f"tool catalogue not found at {path}; reinstall the CLI")


def find_tool(catalog, name):
    section, _, tool_id = name.rpartition("/")
    matches = [t for t in catalog["tools"] if t["id"] == tool_id and (not section or t["section"] == section)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        options = ", ".join(f"{t['section']}/{t['id']}" for t in matches)
        raise CliError(f"'{name}' is ambiguous: {options}", EXIT_USAGE)
    ids = [t["id"] for t in catalog["tools"]]
    close = difflib.get_close_matches(tool_id, ids, n=5, cutoff=0.6) or [i for i in ids if tool_id in i][:5]
    hint = f" Similar: {', '.join(close)}." if close else " Run `snapotter tools --search <word>`."
    raise CliError(f"unknown tool '{name}'.{hint}", EXIT_USAGE)


def option_index(tool):
    """name -> (kind, spec) for every option the tool documents."""
    index = {s["name"]: ("settings", s) for s in tool.get("settings", [])}
    for f in tool.get("fields", []):
        index[f["name"]] = ("field", f)
    return index


def parse_value(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def build_options(tool, pairs, warn):
    """--set k=v pairs -> (settings dict, fields dict). Validates what it can."""
    index = option_index(tool)
    settings, fields = {}, {}
    for pair in pairs or []:
        if "=" not in pair:
            raise CliError(f"--set expects name=value, got '{pair}'", EXIT_USAGE)
        name, raw = pair.split("=", 1)
        name = name.strip()
        value = parse_value(raw)
        if name in index:
            kind, spec = index[name]
            choices = spec.get("choices")
            if choices and str(value) not in choices:
                raise CliError(f"{tool['id']}: '{name}' must be one of {', '.join(choices)} (got '{raw}')", EXIT_USAGE)
            for bound, ok in (("minimum", lambda v, b: v >= b), ("maximum", lambda v, b: v <= b)):
                if bound in spec and isinstance(value, (int, float)) and not isinstance(value, bool):
                    if not ok(value, spec[bound]):
                        raise CliError(f"{tool['id']}: '{name}' {bound} is {spec[bound]} (got {value})", EXIT_USAGE)
            (fields if kind == "field" else settings)[name] = value
        elif tool.get("settingsText") or tool.get("unparsed"):
            warn(f"'{name}' is not in the catalogue's parsed settings for {tool['id']}; passing it through unchecked")
            settings[name] = value
        else:
            known = ", ".join(index) or "none"
            raise CliError(f"{tool['id']} has no option '{name}'. Options: {known}", EXIT_USAGE)
    for spec, given in [(f, fields) for f in tool.get("fields", [])] + [(s, settings) for s in tool.get("settings", [])]:
        if spec.get("required") and spec["name"] not in given:
            hint = f" (one of: {', '.join(spec['choices'])})" if spec.get("choices") else ""
            raise CliError(f"{tool['id']}: '{spec['name']}' is required: --set {spec['name']}=...{hint}", EXIT_USAGE)
    return settings, fields


# ── HTTP ─────────────────────────────────────────────────────────────────────


def default_key_file():
    if os.environ.get("SNAPOTTER_KEY_FILE"):
        return os.environ["SNAPOTTER_KEY_FILE"]
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, ".config", "snapotter", "key")


def read_key(path):
    try:
        with open(path, encoding="ascii") as fh:
            key = fh.read().strip()
    except FileNotFoundError:
        raise CliError(f"API key file not found: {path} (run Install-SnapotterCli.ps1)")
    if not key:
        raise CliError(f"API key file is empty: {path}")
    return key


def is_loopback(host):
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class MultipartBody:
    """A streamed multipart/form-data body with a known length, so multi-GB
    uploads never sit in memory."""

    def __init__(self, parts):
        self.boundary = "snapotter-" + uuid.uuid4().hex
        self._segments = []
        for name, value in parts:
            if isinstance(value, tuple):  # (filename, path)
                filename, path = value
                ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                head = (f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                        f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n").encode("utf-8")
                self._segments += [head, ("file", path), b"\r\n"]
            else:
                text = value if isinstance(value, str) else json.dumps(value)
                self._segments.append((f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"{name}\""
                                       f"\r\n\r\n{text}\r\n").encode("utf-8"))
        self._segments.append(f"--{self.boundary}--\r\n".encode("ascii"))
        self.length = sum(len(s) if isinstance(s, bytes) else os.path.getsize(s[1]) for s in self._segments)
        self._iter = None
        self._buf = b""
        self.sent = 0

    @property
    def content_type(self):
        return f"multipart/form-data; boundary={self.boundary}"

    def _chunks(self):
        for seg in self._segments:
            if isinstance(seg, bytes):
                yield seg
            else:
                with open(seg[1], "rb") as fh:
                    while True:
                        block = fh.read(CHUNK)
                        if not block:
                            break
                        yield block

    def read(self, n=-1):
        if self._iter is None:
            self._iter = self._chunks()
        while n < 0 or len(self._buf) < n:
            try:
                self._buf += next(self._iter)
            except StopIteration:
                break
        if n < 0:
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:n], self._buf[n:]
        self.sent += len(out)
        return out


class Client:
    def __init__(self, base, key, timeout=60):
        url = urllib.parse.urlsplit(base)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise CliError(f"bad server URL '{base}'", EXIT_USAGE)
        self.url = url
        self.key = key
        self.timeout = timeout

    def _conn(self, timeout=None):
        cls = http.client.HTTPSConnection if self.url.scheme == "https" else http.client.HTTPConnection
        return cls(self.url.hostname, self.url.port, timeout=timeout or self.timeout)

    def request(self, method, path, body=None, headers=None, timeout=None, stream=False):
        hdrs = {"Authorization": f"Bearer {self.key}", "User-Agent": f"snapotter-cli/{VERSION}"}
        hdrs.update(headers or {})
        if isinstance(body, MultipartBody):
            hdrs["Content-Type"] = body.content_type
            hdrs["Content-Length"] = str(body.length)
        elif body is not None:
            body = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        conn = self._conn(timeout)
        try:
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
        except (ConnectionError, OSError, http.client.HTTPException) as e:
            conn.close()
            raise CliError(f"cannot reach SnapOtter at {self.url.geturl()}: {type(e).__name__}: {e}", EXIT_UNREACHABLE)
        if stream:
            return conn, resp
        data = resp.read()
        conn.close()
        return resp, data


def error_message(resp, data):
    try:
        body = json.loads(data)
    except ValueError:
        body = None
    if isinstance(body, dict):
        msg = body.get("error") or body.get("message") or json.dumps(body)[:300]
        if body.get("details"):
            msg += f" ({str(body['details'])[:300]})"
        return f"HTTP {resp.status}: {msg}"
    text = data.decode("utf-8", "replace").strip()[:300]
    return f"HTTP {resp.status}: {text or resp.reason}"


# ── jobs ─────────────────────────────────────────────────────────────────────


def wait_for_job(client, job_id, progress, timeout=None):
    """Follow the job's SSE stream to its terminal event; reconnect on drops."""
    deadline = time.time() + timeout if timeout else None

    def pending():
        return CliError(f"job {job_id} still running after {timeout}s (resume: snapotter wait {job_id})",
                        EXIT_PENDING)

    attempts = 0
    while True:
        if deadline and time.time() >= deadline:
            raise pending()
        # The socket timeout doubles as the --timeout deadline, so a quiet
        # stream cannot hold the CLI past it.
        sock_timeout = max(1, deadline - time.time()) if deadline else 3600
        conn, resp = client.request("GET", f"/api/v1/jobs/{urllib.parse.quote(job_id)}/progress",
                                    headers={"Accept": "text/event-stream"}, timeout=sock_timeout, stream=True)
        try:
            if resp.status >= 400:
                raise CliError(f"progress for job {job_id}: {error_message(resp, resp.read())}", EXIT_REJECTED)
            while True:
                if deadline and time.time() >= deadline:
                    raise pending()
                try:
                    line = resp.readline()
                except (TimeoutError, OSError):
                    if deadline and time.time() >= deadline - 0.5:
                        raise pending()
                    break
                if not line:
                    break
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                phase = ev.get("phase") or ev.get("status")
                if phase in ("complete", "completed"):
                    return ev.get("result") or ev
                if phase in ("failed", "canceled", "cancelled"):
                    raise CliError(f"job {job_id} {phase}: {ev.get('error') or ev.get('message') or 'no reason given'}",
                                   EXIT_FAILED)
                if ev.get("percent") is not None:
                    progress(ev.get("percent"), ev.get("stage") or phase)
        finally:
            conn.close()
        attempts += 1
        if attempts > 20:
            raise CliError(f"lost the progress stream for job {job_id} (resume: snapotter wait {job_id})", EXIT_FAILED)
        pause = min(30, 2 * attempts)
        if deadline:
            pause = min(pause, max(0, deadline - time.time()))
        time.sleep(pause)


def free_path(path, force):
    """path, or 'name (1).ext' etc. when it exists and force is off."""
    if force or not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    for i in range(1, 10000):
        candidate = f"{stem} ({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
    raise CliError(f"no free file name near {path}")


def target_path(out, default_dir, filename, force):
    filename = os.path.basename(filename) or "snapotter-output"
    if out:
        if os.path.isdir(out) or out.endswith(("/", "\\")):
            os.makedirs(out, exist_ok=True)
            return free_path(os.path.join(out, filename), force)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        return free_path(out, force)
    return free_path(os.path.join(default_dir, filename), force)


def download(client, url, dest):
    conn, resp = client.request("GET", url, timeout=3600, stream=True)
    try:
        if resp.status >= 400:
            raise CliError(f"download failed: {error_message(resp, resp.read())}", EXIT_REJECTED)
        directory = os.path.dirname(os.path.abspath(dest))
        fd, tmp = tempfile.mkstemp(prefix=".snapotter-", dir=directory)
        size = 0
        try:
            with os.fdopen(fd, "wb") as fh:
                while True:
                    block = resp.read(CHUNK)
                    if not block:
                        break
                    fh.write(block)
                    size += len(block)
            if os.path.exists(dest):  # only reachable with --force
                os.replace(tmp, dest)
            else:
                os.rename(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return size
    finally:
        conn.close()


def finish(client, result, out, default_dir, force):
    """Download the job's output; return (path or None, extra payload fields)."""
    extras = {k: v for k, v in result.items()
              if k not in ("downloadUrl", "previewUrl", "jobId", "savedFileId", "originalSize", "processedSize")}
    url = result.get("downloadUrl")
    if not url:
        return None, None, extras
    filename = urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1])
    dest = target_path(out, default_dir, filename, force)
    size = download(client, url, dest)
    return dest, size, extras


# ── commands ─────────────────────────────────────────────────────────────────


class Ui:
    def __init__(self, as_json, quiet):
        self.as_json, self.quiet = as_json, quiet
        self._last = 0.0

    def warn(self, msg):
        print(f"snapotter: {msg}", file=sys.stderr)

    def progress(self, percent, stage):
        if self.quiet or not sys.stderr.isatty():
            return
        now = time.time()
        if now - self._last > 0.5:
            self._last = now
            print(f"\r  {stage or 'working'} {float(percent):5.1f}%   ", end="", file=sys.stderr, flush=True)

    def done_progress(self):
        if not self.quiet and sys.stderr.isatty() and self._last:
            print("\r" + " " * 40 + "\r", end="", file=sys.stderr, flush=True)
            self._last = 0.0

    def result(self, record):
        if self.as_json:
            print(json.dumps(record, ensure_ascii=False), flush=True)
            return
        if record.get("output"):
            print(record["output"])
        elif record.get("jobId") and record.get("detached"):
            print(f"started job {record['jobId']} (collect it with: snapotter wait {record['jobId']})")
        for k, v in (record.get("result") or {}).items():
            text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            print(f"{k}: {text}")


def submit(client, tool, files, settings, fields):
    if tool["body"] == "none":
        raise CliError(f"{tool['id']} is a guidance route; see `snapotter describe {tool['id']}`", EXIT_USAGE)
    path = tool["path"]
    if tool["body"] == "json":
        if files:
            raise CliError(f"{tool['id']} takes no input files, only --set options", EXIT_USAGE)
        return client.request("POST", path, body={**fields, **settings}, timeout=3600)
    parts = []
    specs = tool["inputs"]
    if not specs:
        raise CliError(f"{tool['id']} has no file inputs in the catalogue", EXIT_USAGE)
    if len(specs) == 1:
        spec = specs[0]
        if len(files) > 1 and not spec["multiple"]:
            raise CliError(f"{tool['id']} takes one file; use --batch to run it on each file", EXIT_USAGE)
        parts += [(spec["field"], (os.path.basename(f), f)) for f in files]
    else:  # several named inputs, e.g. image + mask: positional order
        if len(files) > len(specs):
            raise CliError(f"{tool['id']} takes {', '.join(s['field'] for s in specs)}; got {len(files)} files", EXIT_USAGE)
        for spec, f in zip(specs, files):
            parts.append((spec["field"], (os.path.basename(f), f)))
        missing = [s["field"] for s in specs[len(files):] if s["required"]]
        if missing:
            raise CliError(f"{tool['id']} also needs: {', '.join(missing)} (pass the files in that order)", EXIT_USAGE)
    if settings:
        parts.append(("settings", json.dumps(settings)))
    parts += [(k, v if isinstance(v, str) else json.dumps(v)) for k, v in fields.items()]
    return client.request("POST", path, body=MultipartBody(parts), timeout=3600)


def handle_response(client, ui, resp, data, args, default_dir, record):
    if resp.status >= 400:
        raise CliError(error_message(resp, data), EXIT_REJECTED)
    ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip()
    if ctype == "application/json":
        body = json.loads(data or b"{}")
    else:  # a tool that answers with the file itself
        ext = mimetypes.guess_extension(ctype) or ".bin"
        dest = target_path(args.out, default_dir, f"{record['tool']}{ext}", args.force)
        with open(dest, "wb") as fh:
            fh.write(data)
        record.update(output=dest, bytes=len(data))
        return record
    job_id = body.get("progressJobId") or body.get("jobId")
    if resp.status == 202 or body.get("async"):
        record["jobId"] = body.get("jobId")
        if args.detach:
            record["detached"] = True
            return record
        result = wait_for_job(client, job_id, ui.progress, args.timeout)
        ui.done_progress()
    else:
        result = body
        record["jobId"] = body.get("jobId")
    dest, size, extras = finish(client, result, args.out, default_dir, args.force)
    record.update(output=dest, bytes=size, result=extras or None)
    return record


def cmd_tools(args, catalog, ui):
    tools = catalog["tools"]
    if args.section:
        tools = [t for t in tools if t["section"] == args.section]
    if args.search:
        words = args.search.lower().split()
        tools = [t for t in tools if all(w in f"{t['id']} {t['name']} {t['summary']}".lower() for w in words)]
    if ui.as_json:
        print(json.dumps([{k: t[k] for k in ("id", "section", "name", "summary")} for t in tools], ensure_ascii=False))
        return EXIT_OK
    for t in tools:
        print(f"{t['section']:6} {t['id']:28} {t['name']}")
    print(f"{len(tools)} tool(s)", file=sys.stderr)
    return EXIT_OK


def cmd_describe(args, catalog, ui):
    t = find_tool(catalog, args.tool)
    if ui.as_json:
        print(json.dumps(t, ensure_ascii=False, indent=1))
        return EXIT_OK
    print(f"{t['name']}  ({t['section']}/{t['id']})")
    if t["summary"]:
        print(f"  {t['summary']}")
    if t["inputs"]:
        print("Inputs:")
        for i in t["inputs"]:
            many = ", one or more" if i["multiple"] else ""
            print(f"  {i['field']}{many}{'' if i['required'] else ' (optional)'}: {i['description']}")
    elif t["body"] == "json":
        print("Inputs: none (generator; options only)")
    options = t.get("settings", []) + t.get("fields", [])
    if options:
        print("Options (--set name=value):")
        for o in options:
            bits = [o.get("type") or "any"]
            if "default" in o:
                bits.append(f"default {json.dumps(o['default'])}")
            if o.get("required"):
                bits.append("required")
            if "minimum" in o or "maximum" in o:
                bits.append(f"range {o.get('minimum', '')}..{o.get('maximum', '')}")
            print(f"  {o['name']} ({', '.join(bits)})")
            desc = o.get("description") or ""
            if o.get("choices"):
                print(f"      one of: {', '.join(o['choices'])}")
                desc = re.sub(r"^One of:\s*[^.;]+[.;]?\s*", "", desc)
            if desc:
                print(f"      {desc}")
    if t.get("settingsText"):
        print(f"Options (free text, passed through unchecked): {t['settingsText']}")
    if not options and not t.get("settingsText"):
        print("Options: none")
    return EXIT_OK


def expand_inputs(patterns):
    files = []
    for p in patterns:
        hits = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
        if not hits:
            raise CliError(f"no files match {p}", EXIT_USAGE)
        files += hits
    for f in files:
        if not os.path.isfile(f):
            raise CliError(f"not a file: {f}", EXIT_USAGE)
    return files


def cmd_run(args, catalog, ui, client):
    tool = find_tool(catalog, args.tool)
    settings, fields = build_options(tool, args.set, ui.warn)
    files = expand_inputs(args.inputs)
    if tool["body"] == "multipart" and not files:
        raise CliError(f"{tool['id']} needs an input file", EXIT_USAGE)
    jobs = [[f] for f in files] if args.batch else [files]
    if args.batch and args.out and not (os.path.isdir(args.out) or args.out.endswith(("/", "\\"))):
        raise CliError("with --batch, --out must be a folder", EXIT_USAGE)
    failures = 0
    for batch_files in jobs:
        start = time.time()
        default_dir = os.path.dirname(os.path.abspath(batch_files[0])) if batch_files else os.getcwd()
        record = {"tool": tool["id"], "inputs": batch_files}
        try:
            resp, data = submit(client, tool, batch_files, settings, fields)
            handle_response(client, ui, resp, data, args, default_dir, record)
            record["seconds"] = round(time.time() - start, 2)
            record["ok"] = True
            ui.result(record)
        except CliError as e:
            ui.done_progress()
            if not args.batch:
                raise
            failures += 1
            record.update(ok=False, error=str(e), seconds=round(time.time() - start, 2))
            if ui.as_json:
                print(json.dumps(record, ensure_ascii=False), flush=True)
            else:
                ui.warn(f"{batch_files[0]}: {e}")
    if args.batch:
        ui.warn(f"batch: {len(jobs) - failures}/{len(jobs)} OK")
        return EXIT_FAILED if failures else EXIT_OK
    return EXIT_OK


def cmd_pipeline(args, catalog, ui, client):
    try:
        with open(args.steps, encoding="utf-8") as fh:
            pipeline = json.load(fh)
    except (OSError, ValueError) as e:
        raise CliError(f"cannot read pipeline {args.steps}: {e}", EXIT_USAGE)
    steps = pipeline.get("steps") if isinstance(pipeline, dict) else None
    if not isinstance(steps, list) or not steps:
        raise CliError('pipeline file must be {"steps": [{"toolId": "...", "settings": {...}}, ...]}', EXIT_USAGE)
    for step in steps:
        find_tool(catalog, str(step.get("toolId", "")))
    files = expand_inputs([args.input])
    start = time.time()
    record = {"tool": "pipeline", "steps": [s["toolId"] for s in steps], "inputs": files}
    body = MultipartBody([("file", (os.path.basename(files[0]), files[0])), ("pipeline", json.dumps(pipeline))])
    resp, data = client.request("POST", "/api/v1/pipeline/execute", body=body, timeout=3600)
    handle_response(client, ui, resp, data, args, os.path.dirname(os.path.abspath(files[0])), record)
    record.update(seconds=round(time.time() - start, 2), ok=True)
    ui.result(record)
    return EXIT_OK


def cmd_wait(args, catalog, ui, client):
    start = time.time()
    result = wait_for_job(client, args.job, ui.progress, args.timeout)
    ui.done_progress()
    dest, size, extras = finish(client, result, args.out, os.getcwd(), args.force)
    ui.result({"jobId": args.job, "output": dest, "bytes": size, "result": extras or None,
               "seconds": round(time.time() - start, 2), "ok": True})
    return EXIT_OK


def cmd_status(args, catalog, ui, client):
    report = {"server": client.url.geturl(), "cli": VERSION, "catalogTools": catalog["count"]}
    resp, data = client.request("GET", "/api/v1/health", timeout=15)
    report["health"] = "ok" if resp.status == 200 else error_message(resp, data)
    # The server's own spec is the source the catalogue was built from, so a
    # difference means the catalogue is stale (or the server is).
    resp, data = client.request("GET", "/api/v1/openapi.yaml", timeout=30)
    if resp.status == 200:
        spec_ids = set(re.findall(r"(?m)^  /api/v1/tools/([a-z]+/[a-z0-9-]+):\s*$", data.decode("utf-8", "replace")))
        catalog_ids = {f"{t['section']}/{t['id']}" for t in catalog["tools"]}
        report["notInCatalog"] = sorted(spec_ids - catalog_ids)[:20]
        report["catalogOnly"] = sorted(catalog_ids - spec_ids)[:20]
    else:
        report["spec"] = error_message(resp, data)
    resp, data = client.request("GET", "/api/v1/features", timeout=15)
    if resp.status == 200:
        bundles = json.loads(data).get("bundles", [])
        report["aiBundles"] = {b.get("id"): b.get("status") for b in bundles}
        report["key"] = "ok"
    else:
        report["key"] = error_message(resp, data)
    if ui.as_json:
        print(json.dumps(report))
    else:
        for k, v in report.items():
            print(f"{k}: {json.dumps(v) if isinstance(v, (dict, list)) else v}")
    if report.get("notInCatalog") or report.get("catalogOnly"):
        ui.warn("the catalogue and the server's tool list differ; rebuild the catalogue after an upgrade")
    healthy = report["health"] == "ok" and report.get("key") == "ok"
    return EXIT_OK if healthy else EXIT_REJECTED


def build_parser():
    p = argparse.ArgumentParser(prog="snapotter", description="Run SnapOtter tools on local files.")
    p.add_argument("--version", action="version", version=f"snapotter {VERSION}")
    p.add_argument("--base", help=f"server URL (default {DEFAULT_BASE}; anything but loopback must be given here)")
    p.add_argument("--json", action="store_true", help="machine-readable output, one JSON object per result")
    p.add_argument("--quiet", action="store_true", help="no progress line")
    # The same flags after the subcommand (`snapotter run ... --json`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--base", default=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)
    _add = sub.add_parser
    sub.add_parser = lambda *a, **k: _add(*a, parents=[common], **k)

    t = sub.add_parser("tools", help="list tools")
    t.add_argument("--section", choices=["image", "video", "audio", "pdf", "files"])
    t.add_argument("--search", help="words that must all appear in the id, name or summary")

    d = sub.add_parser("describe", help="show one tool's inputs and options")
    d.add_argument("tool")

    def job_flags(sp):
        sp.add_argument("--out", help="output file, or a folder (default: next to the input)")
        sp.add_argument("--force", action="store_true", help="overwrite an existing output file")
        sp.add_argument("--detach", action="store_true", help="start the job, print its id and return")
        sp.add_argument("--timeout", type=int, help="give up waiting after this many seconds")

    r = sub.add_parser("run", help="run a tool")
    r.add_argument("tool")
    r.add_argument("inputs", nargs="*", help="input files (globs allowed)")
    r.add_argument("--set", action="append", metavar="NAME=VALUE", help="a tool option; repeat for more")
    r.add_argument("--batch", action="store_true", help="one job per input file")
    job_flags(r)

    pl = sub.add_parser("pipeline", help="run a chain of image tools on one file")
    pl.add_argument("steps", help='JSON file: {"steps": [{"toolId": "...", "settings": {...}}, ...]}')
    pl.add_argument("input")
    job_flags(pl)

    w = sub.add_parser("wait", help="collect a detached job's result")
    w.add_argument("job")
    w.add_argument("--out")
    w.add_argument("--force", action="store_true")
    w.add_argument("--timeout", type=int)

    sub.add_parser("status", help="check the server, the key and the catalogue")
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    ui = Ui(args.json, args.quiet)
    try:
        catalog = load_catalog()
        if args.command == "tools":
            return cmd_tools(args, catalog, ui)
        if args.command == "describe":
            return cmd_describe(args, catalog, ui)
        base = args.base or os.environ.get("SNAPOTTER_URL") or DEFAULT_BASE
        host = urllib.parse.urlsplit(base).hostname or ""
        if not args.base and not is_loopback(host):
            raise CliError(f"refusing non-local server {base}; pass it with --base to confirm", EXIT_USAGE)
        client = Client(base, read_key(default_key_file()))
        handlers = {"run": cmd_run, "pipeline": cmd_pipeline, "wait": cmd_wait, "status": cmd_status}
        return handlers[args.command](args, catalog, ui, client)
    except CliError as e:
        ui.done_progress()
        if ui.as_json:
            print(json.dumps({"ok": False, "error": str(e), "exit": e.code}), flush=True)
        print(f"snapotter: {e}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print("\nsnapotter: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:  # output piped into `head` and the like
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
