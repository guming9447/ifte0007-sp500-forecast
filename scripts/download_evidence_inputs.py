"""Download one registered original, verify exact SHA256, fail closed on change.

No credentials, automatic alternative sources, replacement of originals, or git.
Run --verify-local to check a privately held copy without accessing the network.
"""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def digest(body):
    return hashlib.sha256(body).hexdigest()


def safe_path(root, relative):
    root = root.resolve()
    path = (root/relative).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Path must stay within the selected private data directory")
    return path


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Only credential-free HTTPS source URLs are accepted")


def fetch_https(url):
    validate_url(url)
    # Match the decoded entity-body convention, not compressed wire bytes.
    request = Request(url, headers={"Accept-Encoding": "identity"})
    try:
        response = urlopen(request, timeout=45)
    except HTTPError as error:
        response = error
    with response:
        resolved = response.geturl()
        validate_url(resolved)
        body = response.read(50*1024*1024+1)
        if len(body) > 50*1024*1024:
            raise ValueError("Response exceeds50MB; stop")
        return response.status, body, resolved


def verify_local(record, root):
    path = safe_path(root, record["relative_path"])
    if digest(path.read_bytes()) != record["sha256"]:
        raise ValueError(f"HASH_MISMATCH: {record['id']}; no calculation permitted")
    return path


def download_one(record, root, fetch=fetch_https):
    if not record["downloadable"]:
        raise ValueError("This entry is a failed historical response, transcription or local metadata, not a downloadable dataset")
    target = safe_path(root, record["relative_path"])
    if target.exists():
        return verify_local(record, root)  # Never replace an existing local file.
    validate_url(record["url"])
    attempt = safe_path(root, "_download_attempts/"+record["id"]+"/"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"))
    attempt.mkdir(parents=True, exist_ok=False)
    receipt = {"id": record["id"], "requested_url": record["url"],
               "retrieved_utc": datetime.now(timezone.utc).isoformat(), "expected_sha256": record["sha256"]}
    try:
        status, body, resolved = fetch(record["url"])
        receipt.update(status=status, resolved_url=resolved, actual_sha256=digest(body), bytes=len(body))
        (attempt/"response.bin").write_bytes(body)
        if status != 200:
            raise ValueError(f"HTTP_{status}: stop; source response retained privately")
        if digest(body) != record["sha256"]:
            raise ValueError("HASH_MISMATCH: stop. A rolling/revised source is not the frozen input. Do not update the expected hash to force acceptance.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(body)
        receipt["accepted"] = True
    except Exception as error:
        receipt.update(accepted=False, error=str(error))
        raise
    finally:
        (attempt/"receipt.json").write_text(json.dumps(receipt, indent=2)+"\n", encoding="utf-8")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--id", required=True, help="One exact dataset id from source_registry.json")
    parser.add_argument("--data-root", type=Path, required=True, help="PRIVATE directory, not the GitHub draft")
    parser.add_argument("--verify-local", action="store_true")
    args = parser.parse_args()
    root = args.data_root.resolve()
    registry = args.registry.resolve()
    if root == registry.parent.parent or root.is_relative_to(registry.parent.parent):
        raise ValueError("Private data root must be outside the draft repository containing this registry")
    from storage_policy import ensure_storage
    ensure_storage(root)
    entries = json.loads(registry.read_text(encoding="utf-8"))["entries"]
    matches = [r for r in entries if r["id"] == args.id]
    if len(matches) != 1:
        raise ValueError("Unknown or duplicate dataset id")
    path = verify_local(matches[0], root) if args.verify_local else download_one(matches[0], root)
    print("VERIFIED="+str(path))


if __name__ == "__main__":
    main()
