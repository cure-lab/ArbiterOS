"""
Pre-warm ``skill_trust_by_name.json`` by scanning every package under ``skills_root``.

Intended to run immediately before ``litellm`` (see ``poe litellm`` in ``pyproject.toml``).
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    if os.environ.get("ARBITEROS_SKIP_SKILL_WARM") == "1":
        print(
            "ArbiterOS: skip skill warm-up (ARBITEROS_SKIP_SKILL_WARM=1)",
            file=sys.stderr,
        )
        return 0

    # Local import so ``uv run python -m arbiteros_kernel.warm_skill_trust`` works
    # without pulling heavy deps before we know we need them.
    from arbiteros_kernel.instruction_parsing.tool_parsers import skill_trust

    root = skill_trust.get_skills_root()
    if not root:
        raw = skill_trust.skills_root_raw()
        if raw:
            expanded = os.path.abspath(os.path.expanduser(raw))
            print(
                "ArbiterOS: skills_root is set but is not a directory in this environment "
                f"({expanded!r}); skip warm-up. In Docker, bind-mount host skills and set "
                "ARBITEROS_SKILLS_ROOT to the in-container path.",
                file=sys.stderr,
            )
        else:
            print(
                "ArbiterOS: no skills_root "
                "(arbiteros_skill_trust.skills_root / ARBITEROS_SKILLS_ROOT); skip warm-up.",
                file=sys.stderr,
            )
        return 0

    packages = skill_trust.list_skill_packages(root)
    if not packages:
        print(f"ArbiterOS: no skill packages under {root}; skip warm-up.", file=sys.stderr)
        return 0

    n = len(packages)
    cached_n = 0
    scanned_ok = 0
    failed_n = 0

    try:
        from rich.console import Console
        from rich.progress import (
            BarColumn,
            Progress,
            TaskProgressColumn,
            TextColumn,
            TimeElapsedColumn,
        )

        use_rich = True
    except ImportError:
        use_rich = False

    if use_rich:
        console = Console(stderr=True)
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Starting…", total=n)
            for name, skill_dir in packages:
                was_cached = skill_trust.is_skill_cached(name)
                status = "cached" if was_cached else "scanning"
                progress.update(task, description=f"{name} ({status})")
                t = skill_trust.resolve_trust_for_skill(name, skill_dir)
                if was_cached:
                    cached_n += 1
                elif t is not None:
                    scanned_ok += 1
                else:
                    failed_n += 1
                progress.advance(task)
    else:
        for i, (name, skill_dir) in enumerate(packages, start=1):
            was_cached = skill_trust.is_skill_cached(name)
            print(
                f"[{i}/{n}] {name} ({'cached' if was_cached else 'scanning'}) …",
                file=sys.stderr,
                flush=True,
            )
            t = skill_trust.resolve_trust_for_skill(name, skill_dir)
            if was_cached:
                cached_n += 1
            elif t is not None:
                scanned_ok += 1
            else:
                failed_n += 1

    print(
        f"ArbiterOS: skill warm-up done — total={n}, already_cached={cached_n}, "
        f"newly_scanned={scanned_ok}, failed={failed_n}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
