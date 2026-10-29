# snapotter CLI

A command-line client for the local SnapOtter stack on the Raider, and the
`snapotter` Claude skill that drives it. Standard-library Python; no packages
to install at run time.

| File | Purpose |
|---|---|
| `snapotter.py` | The CLI (`tools`, `describe`, `run`, `pipeline`, `wait`, `status`). |
| `tools.json` | Tool catalogue generated from `apps/api/src/openapi.yaml` (254 tools). |
| `build_catalog.py` | Regenerates `tools.json`. Needs PyYAML. |
| `Install-SnapotterCli.ps1` | Installs the CLI to `%USERPROFILE%\.local\bin\snapotter\` and creates the scoped API key. |
| `Verify-Cli.ps1` | 19 live checks against the running stack. |
| `skill/SKILL.md` | The Claude skill. The copy in Craig's account skills is the one Claude uses; this is its source. |
| `tests/` | Unit tests against a fake server. |

## Install or refresh (PowerShell on the Raider)

Run from `C:\Users\cfish\win-projects\snapotter-convert-anything`:

```powershell
python tools\snapotter-cli\build_catalog.py
.\tools\snapotter-cli\Install-SnapotterCli.ps1
.\tools\snapotter-cli\Verify-Cli.ps1
```

The installer keeps an existing key. `-RotateKey` replaces it (the old key
stays valid until revoked in SnapOtter's settings). The key is `tools:use`
only, expires 12 months after creation, and lives in
`C:\Users\cfish\.config\snapotter\key`, readable only by you.

## Use

```powershell
snapotter tools --search "compress video"
snapotter describe compress-video
snapotter run compress-video clip.mp4 --set quality=strong
snapotter run remove-background --batch photos\*.jpg
snapotter run compress-video big.mp4 --detach --json   # then: snapotter wait <jobId>
snapotter status
```

Results are written next to the input with SnapOtter's suffix and never
overwrite an existing file unless `--force` is given. `--json` prints one
object per result. Exit codes: 0 ok, 1 error, 2 usage, 3 rejected by the
server, 4 job failed, 5 server unreachable, 130 interrupted.

## Tests (any machine)

```bash
python3 -m unittest discover -s tools/snapotter-cli/tests
```

## Known limits

- Accurate OCR tiers report `unsupported-host` on the fork's image; only fast
  (Tesseract) OCR works. See the PRD's open items.
- One media job runs at a time (`CONCURRENT_JOBS=3`, 8 NVENC sessions).
