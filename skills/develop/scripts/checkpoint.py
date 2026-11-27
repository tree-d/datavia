"""File-level checkpoint for rolling back an agent's edits.

Before an agent edits a file for the first time while executing a plan, it
saves the file's current content here (or records that the file is new).
``restore`` puts every saved file back and deletes the new ones, so the
working tree returns to the state before the plan was executed — including
any uncommitted changes the user had before, which ``git checkout`` would
destroy.

The checkpoint lives in ``<git dir>/dev-checkpoint/`` and is never tracked.

Usage (from anywhere inside the repository)::

    python3 skills/develop/scripts/checkpoint.py start [--force]
    python3 skills/develop/scripts/checkpoint.py save PATH [PATH ...]
    python3 skills/develop/scripts/checkpoint.py list
    python3 skills/develop/scripts/checkpoint.py diff
    python3 skills/develop/scripts/checkpoint.py restore [PATH ...]
    python3 skills/develop/scripts/checkpoint.py path
    python3 skills/develop/scripts/checkpoint.py clear
"""

import argparse
import difflib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Name of the checkpoint directory inside the Git directory.
_CHECKPOINT_DIR_NAME: str = "dev-checkpoint"

#: File in the checkpoint directory that records what was saved.
_MANIFEST_NAME: str = "manifest.json"


def _git(*args: str) -> str:
    """Run a Git command and return its stripped standard output.

    Parameters
    ----------
    *args : str
        Arguments after ``git``.

    Returns
    -------
    str
        Standard output without surrounding whitespace.

    Raises
    ------
    SystemExit
        If the command fails (e.g. not inside a Git repository).
    """
    # Fixed executable and argument list, no shell: nothing to inject.
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


class Checkpoint:
    """Saved originals of the files an agent edits, plus a manifest.

    Parameters
    ----------
    repo_root : Path
        Top-level directory of the working tree.
    checkpoint_dir : Path
        Directory holding the manifest and the saved copies.
    """

    def __init__(self, repo_root: Path, checkpoint_dir: Path) -> None:
        """Store the locations; does not read or create anything."""
        self.repo_root = repo_root
        self.checkpoint_dir = checkpoint_dir
        self.files_dir = checkpoint_dir / "files"
        self.manifest_path = checkpoint_dir / _MANIFEST_NAME

    @classmethod
    def for_current_repo(cls) -> "Checkpoint":
        """Return the checkpoint of the repository containing the working directory.

        Returns
        -------
        Checkpoint
            Checkpoint located in ``<git dir>/dev-checkpoint``.
        """
        repo_root = Path(_git("rev-parse", "--show-toplevel"))
        git_dir = Path(_git("rev-parse", "--absolute-git-dir"))
        return cls(repo_root, git_dir / _CHECKPOINT_DIR_NAME)

    def exists(self) -> bool:
        """Return whether a checkpoint has been started."""
        return self.manifest_path.is_file()

    def start(self, force: bool) -> None:
        """Begin a new, empty checkpoint.

        Parameters
        ----------
        force : bool
            Discard an existing checkpoint instead of refusing.

        Raises
        ------
        SystemExit
            If a checkpoint exists and *force* is false.
        """
        if self.exists() and not force:
            raise SystemExit(
                "A checkpoint already exists. Restore or clear it first, "
                "or pass --force to discard it."
            )
        shutil.rmtree(self.checkpoint_dir, ignore_errors=True)
        self.files_dir.mkdir(parents=True)
        manifest = {
            "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "head": _git("rev-parse", "HEAD"),
            "files": {},
            "new_dirs": [],
        }
        self._write_manifest(manifest)
        print(f"Checkpoint started in {self.checkpoint_dir}")

    def save(self, paths: list[str]) -> None:
        """Record the original state of *paths* unless already recorded.

        Existing files are copied; missing ones are recorded as new, together
        with any parent directories that don't exist yet.

        Parameters
        ----------
        paths : list[str]
            Files about to be edited or created.
        """
        manifest = self._read_manifest()
        for raw in paths:
            relative = self._relative(raw)
            key = relative.as_posix()
            if key in manifest["files"]:
                continue  # first save wins: it holds the pre-plan state
            absolute = self.repo_root / relative
            if absolute.is_file():
                target = self.files_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(absolute, target)
                manifest["files"][key] = "saved"
            elif absolute.exists():
                raise SystemExit(f"{key} is not a regular file")
            else:
                manifest["files"][key] = "new"
                manifest["new_dirs"].extend(self._missing_parents(relative))
            print(f"{manifest['files'][key]:>5}  {key}")
        self._write_manifest(manifest)

    def list_files(self) -> None:
        """Print every recorded file and whether it was saved or is new."""
        manifest = self._read_manifest()
        print(f"Checkpoint from {manifest['created_utc']} (HEAD {manifest['head']})")
        for key, state in sorted(manifest["files"].items()):
            print(f"{state:>5}  {key}")

    def diff(self) -> None:
        """Print a unified diff from the recorded originals to the current files."""
        manifest = self._read_manifest()
        for key, state in sorted(manifest["files"].items()):
            before = (
                (self.files_dir / key).read_text(errors="replace").splitlines(True)
                if state == "saved"
                else []
            )
            current = self.repo_root / key
            after = (
                current.read_text(errors="replace").splitlines(True)
                if current.is_file()
                else []
            )
            sys.stdout.writelines(
                difflib.unified_diff(before, after, f"a/{key}", f"b/{key}")
            )

    def restore(self, paths: list[str]) -> None:
        """Put recorded files back in their original state.

        Parameters
        ----------
        paths : list[str]
            Files to restore; empty means all recorded files.  Restored files
            are removed from the manifest.
        """
        manifest = self._read_manifest()
        keys = (
            [self._relative(raw).as_posix() for raw in paths]
            if paths
            else sorted(manifest["files"])
        )
        for key in keys:
            state = manifest["files"].pop(key, None)
            current = self.repo_root / key
            if state == "saved":
                current.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.files_dir / key, current)
                print(f"restored  {key}")
            elif state == "new":
                current.unlink(missing_ok=True)
                print(f" deleted  {key}")
            else:
                print(f"  ignored  {key} (not in checkpoint)")
        # Deepest first, so nested new directories are removed bottom-up.
        for directory in sorted(set(manifest["new_dirs"]), key=len, reverse=True):
            path = self.repo_root / directory
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
                print(f" deleted  {directory}/")
        if not paths:
            manifest["new_dirs"] = []
        self._write_manifest(manifest)

    def clear(self) -> None:
        """Delete the checkpoint (after the user accepted the result)."""
        shutil.rmtree(self.checkpoint_dir, ignore_errors=True)
        print("Checkpoint cleared.")

    def _relative(self, raw: str) -> Path:
        """Return *raw* relative to the repository root.

        Raises
        ------
        SystemExit
            If the path lies outside the repository.
        """
        absolute = (Path.cwd() / raw).resolve()
        try:
            return absolute.relative_to(self.repo_root.resolve())
        except ValueError:
            raise SystemExit(f"{raw} is outside the repository") from None

    def _missing_parents(self, relative: Path) -> list[str]:
        """Return the parent directories of *relative* that don't exist yet."""
        return [
            parent.as_posix()
            for parent in relative.parents
            if parent != Path(".") and not (self.repo_root / parent).exists()
        ]

    def _read_manifest(self) -> dict[str, Any]:
        """Load the manifest.

        Raises
        ------
        SystemExit
            If no checkpoint has been started.
        """
        if not self.exists():
            raise SystemExit("No checkpoint. Run `checkpoint.py start` first.")
        manifest: dict[str, Any] = json.loads(self.manifest_path.read_text())
        return manifest

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        """Write *manifest* as indented JSON."""
        self.manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line.

    Parameters
    ----------
    argv : list[str]
        Arguments without the program name.

    Returns
    -------
    argparse.Namespace
        ``command`` plus ``paths`` / ``force`` where applicable.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="begin a new checkpoint")
    start.add_argument("--force", action="store_true", help="discard an old one")
    save = commands.add_parser("save", help="record files before editing them")
    save.add_argument("paths", nargs="+")
    commands.add_parser("list", help="show recorded files")
    commands.add_parser("diff", help="diff originals against current files")
    restore = commands.add_parser("restore", help="roll files back")
    restore.add_argument("paths", nargs="*")
    commands.add_parser("path", help="print the checkpoint directory")
    commands.add_parser("clear", help="delete the checkpoint")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Run one checkpoint command.

    Parameters
    ----------
    argv : list[str]
        Arguments without the program name.

    Returns
    -------
    int
        Process exit code.
    """
    args = _parse_args(argv)
    checkpoint = Checkpoint.for_current_repo()
    if args.command == "start":
        checkpoint.start(args.force)
    elif args.command == "save":
        checkpoint.save(args.paths)
    elif args.command == "list":
        checkpoint.list_files()
    elif args.command == "diff":
        checkpoint.diff()
    elif args.command == "restore":
        checkpoint.restore(args.paths)
    elif args.command == "path":
        print(checkpoint.checkpoint_dir)
    elif args.command == "clear":
        checkpoint.clear()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
