"""Copies the slice of a local CMU checkout the web tool needs (gamedata.txt) into gamedata/, for
running the web server without Docker. The Docker image gets the same slice by a sparse checkout.

    python tools/snapshot.py C:\\cmu\\wt-jeeps
"""

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def entries():
    with open(os.path.join(HERE, "gamedata.txt"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                yield line


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="a CMU checkout with the open vehicles (the jeeps branch or later)")
    parser.add_argument("--out", default=os.path.join(HERE, "gamedata"))
    args = parser.parse_args()
    if not os.path.isdir(os.path.join(args.repo, "Content.CMU")):
        sys.exit(f"{args.repo} isn't a CMU checkout (no Content.CMU).")
    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    for entry in entries():
        src = os.path.join(args.repo, *entry.rstrip("/").split("/"))
        dst = os.path.join(args.out, *entry.rstrip("/").split("/"))
        if entry.endswith("/"):
            if not os.path.isdir(src):
                sys.exit(f"Missing folder {entry} in {args.repo}.")
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            if not os.path.isfile(src):
                sys.exit(f"Missing file {entry} in {args.repo}.")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        print(entry)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
