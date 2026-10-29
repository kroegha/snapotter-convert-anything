"""Upload rebuilt bundles to the fork's private Hugging Face repo.

Usage (inside the snapotter-local image; token file mounted read-only):
    HF_TOKEN_PATH=/run/secrets/hf_token /opt/venv/bin/python -I \
        /repo/scripts/blackwell/publish_bundles.py /bundles/upload-plan.json

Reads the repo from the manifest's bundleRepo (kroegha/snapotter-bundles-cu134),
creates it private if missing, and uploads each archive to the path the
manifest now names. A file already on the repo with the same SHA-256 is
skipped, so the script can be re-run after an interruption.
"""
import json
import os
import sys

from huggingface_hub import HfApi

MANIFEST = "/repo/docker/feature-manifest.json"


def main():
    plan = json.load(open(sys.argv[1]))
    repo = json.load(open(MANIFEST))["bundleRepo"]
    api = HfApi()
    who = api.whoami()
    print(f"authenticated as {who.get('name')}; target {repo}", flush=True)
    api.create_repo(repo, repo_type="model", private=True, exist_ok=True)

    existing = {}
    try:
        for info in api.list_repo_tree(repo, repo_type="model", recursive=True, expand=True):
            lfs = getattr(info, "lfs", None)
            if lfs is not None:
                existing[info.path] = getattr(lfs, "sha256", None)
    except Exception as e:  # empty repo
        print(f"(listing repo: {e})", flush=True)

    for item in plan:
        path = item["path_in_repo"]
        if existing.get(path) == item["sha256"]:
            print(f"= {path} already uploaded", flush=True)
            continue
        size = os.path.getsize(item["local"]) / 2**30
        print(f"> {path} ({size:.2f} GiB)", flush=True)
        api.upload_file(
            path_or_fileobj=item["local"],
            path_in_repo=path,
            repo_id=repo,
            repo_type="model",
            commit_message=f"{item['bundle']} amd64-gpu (CUDA 13.4, sha256 {item['sha256'][:12]})",
        )
    print("publish complete", flush=True)


if __name__ == "__main__":
    main()
