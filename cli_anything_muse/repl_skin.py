"""Unified terminal REPL skin for CLI-Anything harnesses (Muse-native)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

ACCENT_COLORS: dict[str, str] = {
    "gimp": "\033[38;5;214m",
    "blender": "\033[38;5;208m",
    "inkscape": "\033[38;5;39m",
    "audacity": "\033[38;5;33m",
    "libreoffice": "\033[38;5;40m",
    "kdenlive": "\033[38;5;141m",
    "obs": "\033[38;5;196m",
    "wiremock": "\033[38;5;118m",
    "go_bot": "\033[38;5;45m",
}
DEFAULT_ACCENT = "\033[38;5;75m"  # sky-blue fallback
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def visible_len(text: str) -> int:
    return len(strip_ansi(text))


class ReplSkin:
    """Consistent terminal UI, prompt, banner, and formatting helper."""

    INNER_WIDTH = 72

    def __init__(
        self,
        software: str,
        version: str = "1.0.0",
        history_file: str | Path | None = None,
        skill_path: str | Path | None = None,
        use_color: bool | None = None,
    ) -> None:
        raw = software.strip()
        self.software_slug = raw.lower().replace("_", "-")
        self.software = raw.lower().replace("-", "_")
        self.display_name = raw.replace("-", " ").replace("_", " ").title()
        self.version = version
        self.accent = ACCENT_COLORS.get(self.software, DEFAULT_ACCENT)
        self._explicit_skill_path = Path(skill_path) if skill_path else None
        self.history_file = (
            Path(history_file)
            if history_file
            else Path.home() / f".cli-anything-{self.software_slug}" / "history"
        )
        if use_color is None:
            self.color_enabled = self._detect_color()
        else:
            self.color_enabled = use_color

    @staticmethod
    def _detect_color() -> bool:
        if os.environ.get("NO_COLOR") or os.environ.get("CLI_ANYTHING_NO_COLOR"):
            return False
        return bool(getattr(sys.stdout, "isatty", lambda: False)())

    def _c(self, code: str, text: str) -> str:
        if not self.color_enabled:
            return text
        return f"{code}{text}{RESET}"

    def resolve_skill_path(self) -> Path | None:
        """Prefer repo-root canonical skill, then Muse custom skill, then packaged copy."""
        if self._explicit_skill_path and self._explicit_skill_path.exists():
            return self._explicit_skill_path

        skill_dir_name = f"cli-anything-{self.software_slug}"
        module_path = Path(__file__).resolve()
        for parent in module_path.parents:
            canonical = parent / "skills" / skill_dir_name / "SKILL.md"
            if canonical.exists():
                return canonical

        muse_custom = Path.home() / "workspace" / "custom-skills" / skill_dir_name / "SKILL.md"
        if muse_custom.exists():
            return muse_custom

        packaged = module_path.parent.parent / "skills" / "SKILL.md"
        if packaged.exists():
            return packaged
        return None

    @staticmethod
    def _format_home_path(path: Path | None) -> str:
        if path is None:
            return f"~/workspace/custom-skills/cli-anything-<software>/SKILL.md"
        home = Path.home()
        try:
            rel = path.resolve().relative_to(home.resolve())
            return f"~/{rel}"
        except ValueError:
            return str(path)

    def _ensure_history_dir(self) -> Path:
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        return self.history_file

    def _box_line(self, content: str) -> str:
        pad = max(0, self.INNER_WIDTH - visible_len(content))
        return f"│ {content}{' ' * pad} │"

    def banner(self) -> str:
        skill = self.resolve_skill_path()
        skill_display = (
            self._format_home_path(skill)
            if skill is not None
            else f"~/workspace/custom-skills/cli-anything-{self.software_slug}/SKILL.md"
        )
        title = f"{self._c(self.accent + BOLD, '◆ cli-anything')} · {self._c(BOLD, self.display_name)}"
        ver = f"Version: {self.version}"
        skill_line = f"Muse Skill: {skill_display}"
        install_hint = (
            f"Install locally: cp SKILL.md ~/workspace/custom-skills/cli-anything-{self.software_slug}/"
        )
        hint_line = self._c(DIM, "Type help for commands, quit to exit")

        border = "─" * (self.INNER_WIDTH + 2)
        lines = [
            f"╭{border}╮",
            self._box_line(title),
            self._box_line(ver),
            self._box_line(skill_line),
            self._box_line(install_hint[: self.INNER_WIDTH]),
            self._box_line(hint_line),
            f"╰{border}╯",
        ]
        return "\n".join(lines)

    def print_banner(self) -> None:
        print(self.banner())

    def prompt(self, context: str | None = None, modified: bool = False) -> str:
        if not self.color_enabled:
            ctx = f"[{context}{'*' if modified else ''}]" if context else ""
            return f"cli-anything-{self.software_slug}{ctx}> " if ctx else "> "
        mark = self._c(self.accent, "◆")
        name = self._c(self.accent + BOLD, self.software_slug)
        ctx = ""
        if context:
            mod = "*" if modified else ""
            ctx = f"[{self._c(CYAN, context + mod)}]"
        arrow = self._c(self.accent, "❯")
        return f"{mark} {name}{ctx} {arrow} "

    def prompt_tokens(self, context: str | None = None, modified: bool = False) -> list[tuple[str, str]]:
        tokens: list[tuple[str, str]] = [
            ("class:icon", "◆ "),
            ("class:software", self.software_slug),
        ]
        if context:
            tokens.append(("class:brackets", "["))
            tokens.append(("class:context", f"{context}{'*' if modified else ''}"))
            tokens.append(("class:brackets", "]"))
        tokens.append(("class:arrow", " ❯ "))
        return tokens

    @staticmethod
    def bottom_toolbar(items: Sequence[tuple[str, Any]]) -> list[tuple[str, str]]:
        tokens: list[tuple[str, str]] = []
        for idx, (label, value) in enumerate(items):
            if idx:
                tokens.append(("class:bottom-toolbar", "  │  "))
            tokens.append(("class:bottom-toolbar.key", f"{label}: "))
            tokens.append(("class:bottom-toolbar.val", str(value)))
        return tokens

    def create_prompt_session(self, completer: Any = None) -> Any | None:
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
            from prompt_toolkit.history import FileHistory
            from prompt_toolkit.styles import Style
        except ImportError:
            return None

        hist_path = self._ensure_history_dir()
        style = Style.from_dict(
            {
                "icon": "ansibrightcyan bold",
                "software": "ansibrightcyan bold",
                "brackets": "ansigray",
                "context": "ansiyellow",
                "arrow": "ansibrightgreen bold",
                "completion-menu.completion": "bg:#202020 #e0e0e0",
                "completion-menu.completion.current": "bg:#005f87 #ffffff bold",
                "auto-suggest": "#666666",
                "bottom-toolbar": "bg:#1c1c1c #b0b0b0",
                "bottom-toolbar.key": "bg:#1c1c1c #80cbc4 bold",
                "bottom-toolbar.val": "bg:#1c1c1c #ffffff",
            }
        )
        return PromptSession(
            history=FileHistory(str(hist_path)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=completer,
            style=style,
            enable_history_search=True,
        )

    def success(self, message: str) -> None:
        print(f"{self._c(GREEN, '✓')} {message}")

    def error(self, message: str) -> None:
        print(f"{self._c(RED, '✗')} {message}", file=sys.stderr)

    def warning(self, message: str) -> None:
        print(f"{self._c(YELLOW, '▲')} {message}")

    def info(self, message: str) -> None:
        print(f"{self._c(BLUE, '●')} {message}")

    def hint(self, message: str) -> None:
        print(self._c(DIM, message))

    def section(self, title: str) -> None:
        print(f"\n{self._c(self.accent + BOLD, title)}")
        print(self._c(self.accent, "─" * visible_len(title)))

    def status(self, key: str, value: Any) -> None:
        print(f"  {self._c(DIM, key + ':')} {value}")

    def status_block(self, title: str, items: dict[str, Any] | Iterable[tuple[str, Any]]) -> None:
        self.section(title)
        pairs = items.items() if isinstance(items, dict) else items
        for key, value in pairs:
            self.status(str(key), value)

    def progress(self, current: int, total: int, label: str = "") -> None:
        total = max(1, total)
        ratio = min(1.0, max(0.0, current / total))
        filled = int(round(ratio * 20))
        bar = "█" * filled + "░" * (20 - filled)
        pct = int(round(ratio * 100))
        suffix = f" {label}" if label else ""
        print(f"[{self._c(self.accent, bar)}] {pct:3d}%{suffix}")

    def table(self, headers: Sequence[str], rows: Sequence[Sequence[Any]], max_col_width: int = 40) -> None:
        def clip(val: Any) -> str:
            text = str(val)
            if len(text) > max_col_width:
                return text[: max_col_width - 1] + "…"
            return text

        str_headers = [clip(h) for h in headers]
        str_rows = [[clip(cell) for cell in row] for row in rows]
        widths = [len(h) for h in str_headers]
        for row in str_rows:
            for idx, cell in enumerate(row):
                if idx < len(widths):
                    widths[idx] = max(widths[idx], len(cell))

        def fmt_row(cells: Sequence[str]) -> str:
            padded = [
                (cells[i] if i < len(cells) else "").ljust(widths[i])
                for i in range(len(widths))
            ]
            return "│ " + " │ ".join(padded) + " │"

        top = "┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
        mid = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
        bot = "└─" + "─┴─".join("─" * w for w in widths) + "─┘"

        print(top)
        print(fmt_row(str_headers))
        print(mid)
        for row in str_rows:
            print(fmt_row(row))
        print(bot)

    def help(self, commands: dict[str, str] | Sequence[tuple[str, str]]) -> None:
        self.section("Commands")
        pairs = list(commands.items()) if isinstance(commands, dict) else list(commands)
        width = max((len(cmd) for cmd, _ in pairs), default=10) + 2
        for cmd, desc in pairs:
            print(f"  {self._c(self.accent, cmd.ljust(width))} {desc}")

    def goodbye(self) -> None:
        print(self._c(DIM, f"Goodbye from cli-anything-{self.software_slug}."))