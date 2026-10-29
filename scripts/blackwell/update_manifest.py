"""Write freshly built amd64-gpu bundle archives into docker/feature-manifest.json.

Usage (inside the snapotter-local image, from the repo mounted at /repo):
    python3 -I /repo/scripts/blackwell/update_manifest.py \
        /repo/docker/feature-manifest.json /bundles cu134-v1

For every <bundle>-amd64-gpu.tar.gz in the bundles dir it recomputes the
SHA-256 (and checks it against build-bundle.sh's .sha256), measures the
compressed and extracted sizes, and rewrites that bundle's "amd64-gpu" archive
entry to point at <release>/<bundle>-amd64-gpu-r1.tar.gz. Only those entries
change; the rest of the file keeps its formatting. Prints the upload plan as
JSON lines (local file -> path in repo) for publish_bundles.py.
"""
import hashlib
import json
import os
import re
import sys
import tarfile

CHUNK = 8 * 1024 * 1024


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                return h.hexdigest()
            h.update(b)


def extracted_size(path):
    total = 0
    with tarfile.open(path, "r:gz") as tf:
        for member in tf:
            if member.isfile():
                total += member.size
    return total


def replace_archive(text, bundle_id, entry):
    start = text.index(f'\n    "{bundle_id}": {{')
    block_start = text.index('"amd64-gpu": {', start)
    block_end = text.index("}", block_start) + 1
    indent = re.search(r'\n( *)"amd64-gpu"', text[start:block_end]).group(1)
    inner = indent + "  "
    new_block = (
        '"amd64-gpu": {\n'
        f'{inner}"file": "{entry["file"]}",\n'
        f'{inner}"sha256": "{entry["sha256"]}",\n'
        f'{inner}"compressedSize": {entry["compressedSize"]},\n'
        f'{inner}"extractedSize": {entry["extractedSize"]}\n'
        f"{indent}}}"
    )
    return text[:block_start] + new_block + text[block_end:]


def main():
    manifest_path, bundles_dir, release = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(manifest_path) as f:
        text = f.read()
    manifest = json.loads(text)
    plan = []
    for bundle_id, bundle in manifest["bundles"].items():
        if "amd64-gpu" not in bundle.get("archives", {}):
            continue
        archive = os.path.join(bundles_dir, f"{bundle_id}-amd64-gpu.tar.gz")
        if not os.path.exists(archive):
            print(f"skip {bundle_id}: {archive} not built", file=sys.stderr)
            continue
        digest = sha256_of(archive)
        side = archive + ".sha256"
        if os.path.exists(side):
            recorded = open(side).read().split()[0].lower()
            if recorded != digest:
                sys.exit(f"{bundle_id}: sha256 mismatch ({recorded} recorded, {digest} on disk)")
        entry = {
            "file": f"{release}/{bundle_id}-amd64-gpu-r1.tar.gz",
            "sha256": digest,
            "compressedSize": os.path.getsize(archive),
            "extractedSize": extracted_size(archive),
        }
        text = replace_archive(text, bundle_id, entry)
        plan.append({"bundle": bundle_id, "local": archive, "path_in_repo": entry["file"], **entry})
        print(f"{bundle_id}: {entry['compressedSize'] / 2**30:.2f} GiB -> {entry['file']}", file=sys.stderr)

    json.loads(text)  # still valid JSON
    with open(manifest_path, "w") as f:
        f.write(text)
    with open(os.path.join(bundles_dir, "upload-plan.json"), "w") as f:
        json.dump(plan, f, indent=1)
    print(f"updated {len(plan)} bundle(s); plan at {bundles_dir}/upload-plan.json", file=sys.stderr)


if __name__ == "__main__":
    main()
