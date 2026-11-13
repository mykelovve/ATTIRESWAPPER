"""Private attire-swap desktop application using Mykelovve's original 3D engine.

The preview renderer is deliberately loaded only when a model preview is needed.
The bundled Swerve page can be viewed without Blender or a renderer import.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import ntpath
import os
from pathlib import Path
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


APP_NAME = "Mykelovve Attire Swapper"
APP_VERSION = "0.4"
_DLL_HANDLES = []
_PREVIEW_LOCK = threading.Lock()
_DLL_SEARCH_LOCK = threading.Lock()
_RENDERER = None


def resource_path(relative: str) -> Path:
    """Find bundled data, with explicit development fallbacks."""
    candidates = []
    if getattr(sys, "_MEIPASS", None):
        candidates.append(Path(sys._MEIPASS) / relative)
    here = Path(__file__).resolve().parent
    candidates.extend([here / relative, here.parent / relative])
    if relative == "assets/current-preview.html":
        candidates.append(here.parent / "preview-project" / "Swerve_Port_ALL_IN_3D.html")
    elif relative == "assets/WWE2K_MDL":
        candidates.append(here.parent / "blender-addon" / "package" / "WWE2K_MDL")
    elif relative.startswith("assets/helpers/"):
        name = relative.rsplit("/", 1)[-1]
        candidates.extend([here / name, here.parent / "tools" / name])
    elif relative == "assets/helpers":
        candidates.append(here)
    elif relative.startswith("gmf_runtime/"):
        candidates.append(here.parent / "user-formatter" / "runtime" / "mei" / relative)
    return next((p for p in candidates if p.exists()), candidates[0])


def _configuration_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / "MykelovveAttireSwapper"


def _read_preferences() -> dict:
    try:
        value = json.loads((_configuration_dir() / "preferences.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_preferences(value: dict) -> None:
    folder = _configuration_dir()
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / ("preferences-" + secrets.token_hex(4) + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, folder / "preferences.json")


from model_layout import (CharacterChoiceRequired, model_files as _models,
                          attire_choices, resolve_character, preview_layout)


def discover_blender() -> str:
    candidates = []
    found = shutil.which("blender")
    if found:
        candidates.append(Path(found))
    for name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(name)
        if root:
            candidates.extend(sorted((Path(root) / "Blender Foundation").glob("Blender*/blender.exe"), reverse=True))
    return str(next((p for p in candidates if p.is_file()), ""))


def _load_renderer():
    global _RENDERER
    if _RENDERER is not None:
        return _RENDERER
    # The packaged app has these modules in its PYZ. The extra paths allow the
    # same source to be exercised with the user's privately extracted runtime.
    private_runtime = Path(__file__).resolve().parent.parent / "user-formatter" / "runtime"
    if private_runtime.is_dir() and not getattr(sys, "frozen", False):
        sys.path[:0] = [str(private_runtime / "apptree"), str(private_runtime / "mei")]
    roots = [Path(sys._MEIPASS)] if getattr(sys, "_MEIPASS", None) else []
    roots.append(private_runtime / "mei")
    for root in roots:
        dll = root / "bin" / "VCModel.dll"
        if dll.is_file():
            os.environ.setdefault("GMF_VCMODEL_DLL", str(dll))
            if hasattr(os, "add_dll_directory"):
                for directory in (root / "bin", root / "numpy.libs"):
                    if directory.is_dir():
                        _DLL_HANDLES.append(os.add_dll_directory(str(directory)))
            break
    import gmf_preview
    from ui_texture_preview import install
    _RENDERER = install(gmf_preview)
    return _RENDERER


def build_preview(character: str, attire: str, destination: Path | None = None) -> dict:
    """Read models with the original formatter renderer; never convert inputs."""
    with _PREVIEW_LOCK:
        from ui_texture_preview import reset_diagnostics, inspect_preview, annotate_failures
        renderer = _load_renderer()
        reset_diagnostics()
        with preview_layout(character, attire) as (preview_root, preview_attire):
            result = renderer.build(str(preview_root), preview_attire)
        page = Path(result["html"])
        if not page.is_file():
            raise RuntimeError("The preview engine did not produce an HTML page.")
        picture_report = inspect_preview(page)
        annotate_failures(page, picture_report)
        result = dict(result, texture_validation=picture_report)
        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(page, destination)
            result = dict(result, html=str(destination))
        return result


class PreviewHost:
    """Serve only registered standalone pages through tokenized loopback URLs."""

    def __init__(self):
        self.pages: dict[str, Path] = {}
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                expected = "127.0.0.1:%d" % self.server.server_address[1]
                if self.headers.get("Host") != expected:
                    return self.respond(403, b"")
                path = urlsplit(self.path).path
                with owner.lock:
                    page = owner.pages.get(path)
                if page is None:
                    return self.respond(404, b"")
                try:
                    body = page.read_bytes()
                except OSError:
                    return self.respond(410, b"This preview is no longer available.")
                self.respond(200, body, "text/html; charset=utf-8")

            def respond(self, status, body, content_type="text/plain; charset=utf-8"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        atexit.register(self.close)

    def register(self, page: Path) -> str:
        if not page.is_file():
            raise FileNotFoundError("The bundled preview page is missing.")
        path = "/" + secrets.token_hex(24) + "/preview.html"
        with self.lock:
            self.pages[path] = page.resolve()
        return "http://127.0.0.1:%d%s" % (self.server.server_address[1], path)

    def close(self):
        if self.server is not None:
            server, self.server = self.server, None
            server.shutdown()
            server.server_close()


def open_preview_window(url: str) -> None:
    for key in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        value = os.environ.get(key)
        if not value:
            continue
        edge = Path(value) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if edge.is_file():
            profile = _configuration_dir() / "preview-window"
            profile.mkdir(parents=True, exist_ok=True)
            subprocess.Popen([str(edge), "--app=" + url, "--user-data-dir=" + str(profile),
                              "--no-first-run", "--no-default-browser-check", "--window-size=1440,920"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return
    if not webbrowser.open(url):
        raise RuntimeError("No browser could be opened. Install Microsoft Edge or set a default browser.")


def blender_command(blender: str, job: Path) -> list[str]:
    return [str(blender), "--background", "--factory-startup", "--disable-autoexec",
            "--python-exit-code", "1", "--python",
            str(resource_path("assets/helpers/swap_worker.py")), "--", str(job)]


def blender_environment() -> dict:
    """Let Blender load its own DLLs while keeping renderer helpers unchanged."""
    environment = os.environ.copy()
    bundle = getattr(sys, "_MEIPASS", None)
    if os.name == "nt" and getattr(sys, "frozen", False) and bundle:
        normalize = lambda value: ntpath.normcase(ntpath.normpath(value.strip('"')))
        bundled_paths = {normalize(str(bundle)), normalize(ntpath.join(str(bundle), "gmf_runtime"))}
        environment["PATH"] = ";".join(part for part in environment.get("PATH", "").split(";")
                                         if normalize(part) not in bundled_paths)
    return environment


def spawn_blender(command: list[str]):
    """Undo PyInstaller's process-wide DLL search override only during spawn.

    Windows propagates SetDllDirectory to child processes. Its exact previous
    value is restored before returning, so the same app can still build previews.
    """
    arguments = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                     encoding="utf-8", errors="replace", bufsize=1,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     env=blender_environment())
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return subprocess.Popen(command, **arguments)
    import ctypes
    from ctypes import wintypes
    with _DLL_SEARCH_LOCK:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
        kernel32.GetDllDirectoryW.restype = wintypes.DWORD
        kernel32.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
        kernel32.SetDllDirectoryW.restype = wintypes.BOOL
        needed = kernel32.GetDllDirectoryW(0, None)
        buffer = ctypes.create_unicode_buffer(needed + 1)
        kernel32.GetDllDirectoryW(len(buffer), buffer)
        previous = buffer.value or None
        if not kernel32.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return subprocess.Popen(command, **arguments)
        finally:
            if not kernel32.SetDllDirectoryW(previous):
                raise ctypes.WinError(ctypes.get_last_error())


class AttireSwapperApp:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.root = tk, ttk, root
        self.events = queue.Queue()
        self.busy = False
        self.update_busy = False
        self.pending_update = None
        self.process = None
        self.host = None
        self.last_result = None
        self.last_output = None
        self.controls = []
        self.temp = Path(tempfile.mkdtemp(prefix="mykelovve-attire-swap-"))
        self.preferences = _read_preferences()
        self.base = tk.StringVar(value=self.preferences.get("base", ""))
        self.donor = tk.StringVar(value=self.preferences.get("donor", ""))
        self.base_attire = tk.StringVar(value=self.preferences.get("base_attire", ""))
        self.donor_attire = tk.StringVar(value=self.preferences.get("donor_attire", ""))
        self.output = tk.StringVar(value=self.preferences.get("output", str(Path.home() / "Documents" / "Mykelovve Attire Swaps")))
        self.blender = tk.StringVar(value=self.preferences.get("blender", "") or discover_blender())
        self.fit = tk.BooleanVar(value=self.preferences.get("fit_clothing", True))
        self.repair_eyelash = tk.BooleanVar(value=self.preferences.get("repair_eyelash", True))
        self.status = tk.StringVar(value="Ready · open the included Swerve preview, or select your two models.")
        self._style()
        self._layout()
        self._restore_selection("base")
        self._restore_selection("donor")
        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _style(self):
        # Reuse the supplied Formatter's actual appearance-only module.  Do not
        # import its GUI or updater: those also install conversion callbacks.
        private_runtime = Path(__file__).resolve().parent.parent / "user-formatter" / "runtime"
        if private_runtime.is_dir() and not getattr(sys, "frozen", False):
            sys.path[:0] = [str(private_runtime / "apptree"), str(private_runtime / "mei")]
        import gmf_theme
        self.theme = t = gmf_theme
        root, ttk = self.root, self.ttk
        root.title(APP_NAME + " — v" + APP_VERSION)
        width = min(1240, max(900, root.winfo_screenwidth() - 80))
        height = min(860, max(640, root.winfo_screenheight() - 96))
        root.geometry("%dx%d" % (width, height))
        root.minsize(min(1050, width), min(760, height))
        root.configure(background=t.BG)
        root.option_add("*Font", ("Segoe UI", 10))
        style = ttk.Style(root)
        style.theme_use("clam")
        t.style_ttk(root)
        style.configure("TFrame", background=t.BG)
        style.configure("TLabel", background=t.BG, foreground=t.TEXT)
        style.configure("TEntry", fieldbackground=t.ENTRY, foreground=t.TEXT,
                        insertcolor=t.TEXT, bordercolor=t.LINE, padding=(10, 8))
        style.map("TEntry", bordercolor=[("focus", t.ACCENT)],
                  foreground=[("disabled", t.OFF)])
        style.configure("G.TCombobox", padding=(9, 7), arrowsize=13)
        style.configure("TCheckbutton", background=t.PANEL, foreground=t.TEXT, padding=(0, 4))
        style.map("TCheckbutton", background=[("active", t.PANEL)],
                  foreground=[("disabled", t.OFF)], indicatorcolor=[("selected", t.ACCENT)])
        style.configure("Horizontal.TProgressbar", troughcolor=t.FIELD, background=t.ACCENT,
                        bordercolor=t.FIELD, lightcolor=t.ACCENT, darkcolor=t.ACCENT, thickness=4)

        class StatefulPill(t.Pill):
            """Keep the existing worker's ttk state contract on Formatter buttons."""
            def state(button, statespec=None):
                before = str(button.cget("state")) == "disabled"
                if statespec is None:
                    return ("disabled",) if before else ()
                if "disabled" in statespec:
                    button.configure(state="disabled")
                elif "!disabled" in statespec:
                    button.configure(state="normal")
                after = str(button.cget("state")) == "disabled"
                return (("!disabled",) if before else ("disabled",)) if before != after else ()

        self.Pill = StatefulPill

    def _button(self, parent, text, command, **kwargs):
        button = self.Pill(parent, text=text, command=command, **kwargs)
        # Formatter's Canvas buttons retain mouse behavior; also make every new
        # swap action reachable through the keyboard.
        self.tk.Canvas.configure(button, takefocus=1)
        button.bind("<Return>", lambda _: button.invoke())
        button.bind("<space>", lambda _: button.invoke())
        self.controls.append(button)
        return button

    def _label(self, parent, text=None, *, muted=False, font=None, **options):
        options.setdefault("background", parent.cget("background"))
        options.setdefault("foreground", self.theme.DIM if muted else self.theme.TEXT)
        options.setdefault("anchor", "w")
        if text is not None:
            options["text"] = text
        if font is not None:
            options["font"] = font
        return self.tk.Label(parent, **options)

    def _card(self, parent, *, padding=18):
        card = self.tk.Frame(parent, background=self.theme.PANEL, padx=padding, pady=padding)
        self.theme.round_corners(card, 16, parent.cget("background"))
        return card

    def _layout(self):
        tk, ttk, t = self.tk, self.ttk, self.theme
        self.nav_items = []
        side = tk.Frame(self.root, bg=t.SIDE, width=236)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(self.root, bg=t.LINE, width=1).pack(side="left", fill="y")
        main = tk.Frame(self.root, bg=t.BG, padx=26, pady=20)
        main.pack(side="left", fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(5, weight=1)

        # Same artwork, typography, badge and title arrangement as Formatter.
        self._load_brand_logo(side)
        head = tk.Frame(main, bg=t.BG)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        head.columnconfigure(0, weight=1)
        self._label(head, t.tracked("MYKELOVVE"), foreground=t.ACCENT,
                    font=t.SMALL_CAPS_FONT).grid(row=0, column=0, sticky="w")
        self._label(head, "ATTIRE SWAPPER", font=t.TITLE_FONT).grid(row=1, column=0, sticky="w")
        self._label(head, "Base model + ring / entrance attire → WWE 2K26", muted=True,
                    font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=(2, 0))
        update = tk.Frame(head, bg=t.BG)
        update.grid(row=0, column=1, rowspan=3, sticky="ne", padx=(12, 0))
        version = self._label(update, "v" + APP_VERSION + "  PREVIEW", foreground=t.ACCENT_HI,
                              font=("Segoe UI Semibold", 9))
        version.pack(anchor="e", pady=(0, 9))
        t.badge(version, t.ACTIVE, t.ACCENT_HI)
        self.update_button = self.Pill(update, text="Update app", command=self._open_updates, padx=15, pady=6)
        self.update_button.pack(anchor="e")
        tk.Canvas.configure(self.update_button, takefocus=1)
        self.update_button.bind("<Return>", lambda _: self.update_button.invoke())
        self.update_button.bind("<space>", lambda _: self.update_button.invoke())
        self.update_status = self._label(update, "Updates install here", muted=True, font=("Segoe UI", 8))
        self.update_status.pack(anchor="e", pady=(4, 0))

        workspace = self._card(main)
        workspace.grid(row=1, column=0, sticky="ew")
        workspace.columnconfigure(0, weight=1)
        self._label(workspace, t.tracked("MODEL + ATTIRE"), muted=True,
                    font=t.SMALL_CAPS_FONT).grid(row=0, column=0, sticky="w", pady=(0, 10))
        self._selection_row(workspace, 1, "base", "Base model", "Keep this model’s body, head, hair and beard.")
        self._selection_row(workspace, 2, "donor", "Attire donor", "Use this model’s ring and entrance clothing.")
        self._path_row(workspace, 3, "Save new swaps in", self.output, self._browse_output)
        self._path_row(workspace, 4, "Blender executable", self.blender, self._browse_blender)
        self._label(workspace, "Blender 4.3 or 4.4 creates swaps. The 3D viewer opens without Blender.",
                    muted=True, font=("Segoe UI", 9)).grid(row=5, column=0, sticky="w", pady=(5, 0))

        actions = tk.Frame(main, bg=t.BG)
        actions.grid(row=2, column=0, sticky="ew", pady=(16, 12))
        self.swap_button = self._button(actions, "CREATE ATTIRE SWAP", self._start_swap, primary=True)
        self.swap_button.pack(side="left")
        self.result_button = self._button(actions, "See result in 3D", self._preview_result)
        self.result_button.pack(side="left", padx=10)
        self.repair_button = self._button(actions, "Repair options", self._toggle_repairs)
        self.repair_button.pack(side="left")
        self.result_button.state(["disabled"])
        self.repair_options = self._card(main, padding=13)
        self.repair_options.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        for text, variable in (("Fit donor clothing to base (draft)", self.fit),
                               ("Repair missing eyelash texture", self.repair_eyelash)):
            check = ttk.Checkbutton(self.repair_options, text=text, variable=variable)
            check.pack(side="left", padx=(0, 20))
            self.controls.append(check)
        self.repair_options.grid_remove()
        self._repairs_visible = False

        status = tk.Frame(main, bg=t.BG)
        status.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        self.progress = ttk.Progressbar(status, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self._label(status, textvariable=self.status, muted=True,
                    font=("Segoe UI", 9), wraplength=890).pack(anchor="w", pady=(8, 0))

        log_card = self._card(main, padding=14)
        log_card.grid(row=5, column=0, sticky="nsew")
        self._label(log_card, t.tracked("SWAP LOG"), muted=True,
                    font=t.SMALL_CAPS_FONT).pack(anchor="w", pady=(0, 8))
        log_frame = tk.Frame(log_card, bg=t.ENTRY)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=6, bg=t.ENTRY, fg=t.TEXT, insertbackground=t.TEXT,
                           font=("Consolas", 9), relief="flat", borderwidth=0, highlightthickness=0,
                           padx=12, pady=10, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview,
                               style="Slim.Vertical.TScrollbar")
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._label(main, "Original Formatter 3D viewer · WWE2K_MDL / PWM Mods", muted=True,
                    font=("Segoe UI", 8)).grid(row=6, column=0, sticky="w", pady=(10, 0))

        # Sidebar rows run real existing controls, just like Model Formatter.
        # The output control lives here until a completed swap enables it.
        self.output_button = self._button(side, "Open output folder", self._open_output)
        self.output_button.state(["disabled"])
        self.base_preview_button = self._button(side, "Preview base", lambda: self._preview_selected("base"))
        self.donor_preview_button = self._button(side, "Preview donor", lambda: self._preview_selected("donor"))
        self.builtin_preview_button = self._button(side, "Included Swerve", self._preview_builtin)
        self._nav_section(side, "SWAP", (("\ue768", "Create attire swap", self.swap_button, True),
                                        ("\ue8b7", "Output folder", self.output_button, False),
                                        ("\ue90f", "Repair options", self.repair_button, False)))
        self._nav_section(side, "3D VIEWER", (("\ue838", "Preview base", self.base_preview_button, False),
                                             ("\ue838", "Preview donor", self.donor_preview_button, False),
                                             ("\ue838", "Preview result", self.result_button, False),
                                             ("\ue734", "Included Swerve", self.builtin_preview_button, False)))
        self._nav_section(side, "APP", (("\ue895", "Update app", self.update_button, False),))
        footer = tk.Frame(side, bg=t.SIDE)
        footer.pack(side="bottom", fill="x", padx=24, pady=20)
        self._label(footer, "mykelovve", foreground=t.TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
        self._label(footer, "Personal build · v" + APP_VERSION, muted=True, font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))
        self.root.after(250, self._sync_nav)
        self._log("Select your base model and attire donor, then choose the attire scope for each.")
        self._log("Original Formatter viewer: drag to turn · right-drag to move · wheel to zoom.")
        self._log("Standing, Arms up, Flex, Open mouth, Ring and Entrance are available in the 3D window.")
        self._log("Created swaps are review drafts. Check clothing in poses; static previews do not validate game cloth or hair physics.")

    def _selection_row(self, parent, row, kind, title, hint):
        tk, ttk, t = self.tk, self.ttk, self.theme
        frame = tk.Frame(parent, bg=t.PANEL)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        frame.columnconfigure(0, weight=1)
        self._label(frame, title, font=("Segoe UI Semibold", 10)).grid(row=0, column=0, sticky="w")
        self._label(frame, hint, muted=True, font=("Segoe UI", 9)).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 6))
        entry = ttk.Entry(frame, textvariable=getattr(self, kind))
        entry.grid(row=2, column=0, sticky="ew")
        entry.bind("<FocusOut>", lambda _, k=kind: self._refresh_selection(k, quiet=True))
        entry.bind("<Return>", lambda _, k=kind: self._refresh_selection(k))
        self.controls.append(entry)
        setattr(self, kind + "_entry", entry)
        self._button(frame, "Browse…", lambda k=kind: self._browse_character(k), padx=13, pady=7).grid(row=2, column=1, padx=(8, 12))
        combo = ttk.Combobox(frame, textvariable=getattr(self, kind + "_attire"), state="readonly",
                             style="G.TCombobox", width=15)
        combo.grid(row=2, column=2, sticky="e")
        self.controls.append(combo)
        setattr(self, kind + "_combo", combo)

    def _path_row(self, parent, row, title, variable, command):
        tk, ttk, t = self.tk, self.ttk, self.theme
        frame = tk.Frame(parent, bg=t.PANEL)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        self._label(frame, title, font=("Segoe UI", 9), width=18).grid(row=0, column=0, sticky="w", padx=(0, 8))
        entry = ttk.Entry(frame, textvariable=variable)
        entry.grid(row=0, column=1, sticky="ew")
        self.controls.append(entry)
        self._button(frame, "Browse…", command, padx=13, pady=7).grid(row=0, column=2, padx=(8, 0))

    def _load_brand_logo(self, side):
        tk, t = self.tk, self.theme
        logo_path = resource_path("gmf_runtime/thelovvers_logo.png")
        try:
            image = tk.PhotoImage(master=self.root, file=str(logo_path))
            factor = max(1, (max(image.width(), image.height()) + 103) // 104)
            self._brand_logo = image.subsample(factor)
            label = tk.Label(side, bg=t.SIDE, image=self._brand_logo)
        except (OSError, tk.TclError):
            label = self._label(side, "THE LOVVERS", font=("Segoe UI", 14, "bold"))
        label.pack(anchor="w", padx=24, pady=(22, 12))
        icon = resource_path("gmf_runtime/thelovvers_icon.ico")
        if os.name == "nt" and icon.is_file():
            try:
                self.root.iconbitmap(str(icon))
            except tk.TclError:
                pass

    def _nav_section(self, side, title, rows):
        self.theme.side_heading(side, title)
        for icon, text, target, main in rows:
            row = self.theme.NavItem(side, icon, text, target, main=main)
            row.pack(fill="x", padx=12, pady=2)
            self.nav_items.append(row)

    def _sync_nav(self):
        for item in self.nav_items:
            item.sync()
        self.root.after(250, self._sync_nav)

    def _toggle_repairs(self):
        self._repairs_visible = not self._repairs_visible
        if self._repairs_visible:
            self.repair_options.grid()
            self.repair_button.configure(text="Hide repair options")
        else:
            self.repair_options.grid_remove()
            self.repair_button.configure(text="Repair options")

    def _open_updates(self):
        """Check, download and install within the app; never open a website."""
        if self.update_busy:
            self._log("An update check or download is already running.")
            return
        if self.pending_update is not None:
            self._install_update()
            return
        self.update_busy = True
        self.update_status.configure(text="Checking GitHub…")
        self._log("Checking the official Attire Swapper release…")
        def work():
            try:
                from app_updater import check_release, stage_release, cleanup_completed_stages
                cleanup_completed_stages(_configuration_dir() / "updates", APP_VERSION)
                release = check_release(APP_VERSION)
                if release is None:
                    self.events.put(("update_current", None))
                    return
                self.events.put(("log", "Downloading v" + release.version + "; the SHA256 checksum will be verified before installation."))
                previous_percent = -1
                def progress(received, total):
                    nonlocal previous_percent
                    percent = int(received * 100 / total)
                    if percent != previous_percent:
                        previous_percent = percent
                        self.events.put(("update_progress", percent))
                staged = stage_release(release, _configuration_dir() / "updates", progress=progress)
                self.events.put(("update_ready", staged))
            except Exception as error:
                self.events.put(("update_error", str(error)))
        threading.Thread(target=work, name="app-update", daemon=True).start()

    def _install_update(self):
        if self.busy:
            self.update_status.configure(text="Ready after current task")
            self._log("The verified update is ready. Finish the current task, then click Update app to install and restart.")
            return False
        try:
            if not getattr(sys, "frozen", False):
                self.update_status.configure(text="Verified update staged")
                self._log("Development mode: the update was verified and staged at " + self.pending_update["stage"] + ". Run the portable Windows EXE to install updates in place.")
                return False
            from app_updater import prepare_install, launch_installer, wait_installer_ready
            self._save_preferences()
            config = prepare_install(self.pending_update, sys.executable, APP_VERSION)
            installer = launch_installer(config)
            self.update_busy = True
            self._set_busy(True, "Preparing update restart…")
            self.update_status.configure(text="Preparing update restart…")
            def await_installer():
                try:
                    wait_installer_ready(config, installer)
                    self.events.put(("update_installer_ready", None))
                except Exception as error:
                    self.events.put(("update_installer_error", str(error)))
            threading.Thread(target=await_installer, name="update-installer-start", daemon=True).start()
            return False
        except Exception as error:
            self.update_status.configure(text="Update could not install")
            self._error("Could not install app update", error)
            return False

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", str(text).rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _error(self, title, error):
        from tkinter import messagebox
        self._log(str(error))
        messagebox.showerror(title, str(error), parent=self.root)

    def _restore_selection(self, kind):
        if getattr(self, kind).get():
            self._refresh_selection(kind, quiet=True)

    def _refresh_selection(self, kind, quiet=False):
        text = getattr(self, kind).get().strip()
        if not text:
            getattr(self, kind + "_combo")["values"] = []
            getattr(self, kind + "_attire").set("")
            return None
        try:
            try:
                character = resolve_character(text, require_base=(kind == "base"))
            except CharacterChoiceRequired as selection:
                if quiet:
                    raise
                from tkinter import simpledialog
                labels = [str(p.relative_to(selection.selected)) for p in selection.candidates]
                number = simpledialog.askinteger(
                    "Choose a character", "This pack contains several characters. Choose one:\n\n" +
                    "\n".join("%d. %s" % (i + 1, label) for i, label in enumerate(labels)),
                    parent=self.root, minvalue=1, maxvalue=len(labels))
                if number is None:
                    return None
                character = selection.candidates[number - 1]
            attires = attire_choices(character)
            getattr(self, kind).set(str(character))
            getattr(self, kind + "_combo")["values"] = attires
            value = getattr(self, kind + "_attire")
            if value.get() not in attires:
                value.set(attires[0])
            return character
        except (OSError, ValueError) as error:
            getattr(self, kind + "_combo")["values"] = []
            getattr(self, kind + "_attire").set("")
            if not quiet:
                self._error("Choose a character folder", error)
            return None

    def _browse_character(self, kind):
        from tkinter import filedialog
        selected = filedialog.askdirectory(title="Choose " + kind + " character or extracted pack", parent=self.root)
        if selected:
            getattr(self, kind).set(selected)
            self._refresh_selection(kind)

    def _browse_output(self):
        from tkinter import filedialog
        selected = filedialog.askdirectory(title="Choose a folder for new swaps", parent=self.root)
        if selected:
            self.output.set(selected)

    def _browse_blender(self):
        from tkinter import filedialog
        selected = filedialog.askopenfilename(title="Choose Blender 4.3 or 4.4 executable", parent=self.root,
                                              filetypes=[("Blender executable", "blender.exe"), ("All files", "*")])
        if selected:
            self.blender.set(selected)

    def _set_busy(self, active, status="Ready"):
        self.busy = active
        self.status.set(status)
        for control in self.controls:
            control.state(["disabled"] if active else ["!disabled"])
        if not active:
            self.base_combo.configure(state="readonly")
            self.donor_combo.configure(state="readonly")
            if not self.last_result:
                self.result_button.state(["disabled"])
            if not self.last_output:
                self.output_button.state(["disabled"])
            self.progress.stop()

    def _thread(self, action):
        def run():
            try:
                action()
            except Exception as error:
                self.events.put(("error", ("Action could not be completed", str(error))))
                self.events.put(("log", traceback.format_exc()))
            finally:
                self.events.put(("idle", None))
        threading.Thread(target=run, daemon=True).start()

    def _view_page(self, page):
        if self.host is None:
            self.host = PreviewHost()
        open_preview_window(self.host.register(Path(page)))

    def _preview_builtin(self):
        try:
            self._view_page(resource_path("assets/current-preview.html"))
            self._log("Opened the included Swerve draft with the original formatter 3D viewer.")
        except Exception as error:
            self._error("Could not open 3D preview", error)

    def _preview_selected(self, kind):
        character = self._refresh_selection(kind)
        if character is None:
            return
        self._begin_preview(str(character), getattr(self, kind + "_attire").get())

    def _begin_preview(self, character, attire):
        if self.busy:
            return
        self._set_busy(True, "Building 3D preview from the selected model…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        def work():
            built = build_preview(character, attire)
            self.events.put(("preview", built["html"]))
            pictures = built.get("texture_validation", {})
            self.events.put(("log", "%d embedded PNG pictures validated." % pictures.get("embedded_pngs", 0)))
            for missing in pictures.get("missing_mod_pictures", []):
                self.events.put(("log", "Picture missing: %s / %s" % (missing.get("mesh"), missing.get("material"))))
            for missing in pictures.get("missing_alpha_pictures", []):
                self.events.put(("log", "Transparency picture missing: %s / %s" % (missing.get("mesh"), missing.get("material"))))
            for invalid in pictures.get("invalid_textures", []):
                self.events.put(("log", "Picture error: " + str(invalid)))
            for failed in pictures.get("decoder_failures", []):
                self.events.put(("log", "Texture decode failed: %s — %s" % (failed.get("path"), failed.get("error"))))
            problems = built.get("result", {}).get("problems", [])
            self.events.put(("log", "3D preview ready (%d reported pose issue%s). Static preview; physics unverified." %
                             (len(problems), "" if len(problems) == 1 else "s")))
        self._thread(work)

    def _preview_result(self):
        if self.last_result:
            self._begin_preview(self.last_result["output_character"], self.last_result["output_attire"])

    def _start_swap(self):
        if self.busy:
            return
        base = self._refresh_selection("base")
        if base is None:
            return
        donor = self._refresh_selection("donor")
        if donor is None:
            return
        blender = Path(self.blender.get().strip()).expanduser()
        if not self.blender.get().strip() or not blender.is_file():
            self._error("Blender is needed to create swaps", "Select Blender 4.3 or 4.4 using Browse. You can open the included Swerve 3D preview now.")
            return
        worker = resource_path("assets/helpers/swap_worker.py")
        addon = resource_path("assets/WWE2K_MDL")
        if not worker.is_file() or not addon.is_dir():
            self._error("Application files are missing", "The swap worker or WWE2K model addon is missing. Extract the complete application package again.")
            return
        output_text = self.output.get().strip()
        if not output_text:
            self._error("Choose an output folder", "Select a folder where new swaps can be saved.")
            return
        output_parent = Path(output_text).expanduser().resolve()
        # Keep output away from both inputs, including when a user chooses an
        # input's parent folder. Each run always gets a unique destination.
        for source in (base, donor):
            if output_parent == source or source in output_parent.parents:
                self._error("Choose another output folder", "Save swaps outside the source character folders to keep their files intact.")
                return
        try:
            output_parent.mkdir(parents=True, exist_ok=True)
            suffix = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
            output = output_parent / (base.name + "_AttireSwap_" + suffix)
            job_folder = self.temp / suffix
            job_folder.mkdir()
            result_path = job_folder / "result.json"
            config = {"base_character": str(base), "base_attire": self.base_attire.get(),
                      "donor_character": str(donor), "donor_attire": self.donor_attire.get(),
                      "output_dir": str(output), "addon_dir": str(addon),
                      "helpers_dir": str(resource_path("assets/helpers")),
                      "fit_clothing": self.fit.get(), "repair_eyelash": self.repair_eyelash.get(),
                      "result_path": str(result_path)}
            job = job_folder / "job.json"
            job.write_text(json.dumps(config, indent=2), encoding="utf-8")
        except OSError as error:
            self._error("Could not prepare output", error)
            return
        self._save_preferences()
        self.progress.configure(mode="determinate", value=0)
        self._set_busy(True, "Creating attire swap… source model files are read only.")
        self._log("Creating new draft: " + str(output))
        self._thread(lambda: self._run_swap(str(blender), job, result_path))

    def _run_swap(self, blender, job, result_path):
        log_file = job.parent / "blender.log"
        with log_file.open("w", encoding="utf-8") as log:
            self.process = spawn_blender(blender_command(blender, job))
            for line in self.process.stdout:
                log.write(line)
                log.flush()
                line = line.rstrip()
                if line.startswith("SWAP_PROGRESS "):
                    try:
                        self.events.put(("progress", json.loads(line[len("SWAP_PROGRESS "):])))
                    except ValueError:
                        self.events.put(("log", line))
                elif line:
                    self.events.put(("log", line))
            code = self.process.wait()
            self.process = None
        if not result_path.is_file():
            raise RuntimeError("Blender stopped without a swap result (exit %s). Details: %s" % (code, log_file))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if code != 0 or not result.get("success"):
            raise RuntimeError(str(result.get("error") or "Blender could not finish the swap.") + "\nDetails: " + str(log_file))
        for key in ("output_character", "output_attire", "output_root"):
            if not result.get(key):
                raise RuntimeError("The swap worker returned an incomplete result: " + key)
        self.events.put(("result", result))

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._log(value)
                elif kind == "progress":
                    fraction = max(0, min(1, float(value.get("fraction", 0))))
                    self.progress.configure(value=100 * fraction)
                    self.status.set(value.get("step", "Creating swap…"))
                    self._log(value.get("step", "Creating swap…"))
                elif kind == "preview":
                    try:
                        self._view_page(value)
                    except Exception as error:
                        self._error("Could not open the preview window", error)
                elif kind == "result":
                    self.last_result = value
                    self.last_output = value["output_root"]
                    self.progress.configure(value=100)
                    self._log("Draft created: " + value["output_root"])
                    for warning in value.get("warnings", []):
                        self._log("Review: " + str(warning))
                    self._log("Open Preview result to inspect Ring, Entrance and the pose controls. Hair and cloth physics remain unverified.")
                elif kind == "error":
                    self._error(*value)
                elif kind == "idle":
                    self._set_busy(False, "Ready · draft created; inspect the 3D result." if self.last_result else "Ready")
                elif kind == "update_progress":
                    self.update_status.configure(text="Downloading update · %d%%" % value)
                elif kind == "update_current":
                    self.update_busy = False
                    self.update_status.configure(text="You have the latest version")
                    self._log("Attire Swapper v" + APP_VERSION + " is up to date.")
                elif kind == "update_ready":
                    self.update_busy = False
                    self.pending_update = value
                    self.update_status.configure(text="Update verified · restarting…")
                    if self._install_update():
                        return
                elif kind == "update_error":
                    self.update_busy = False
                    self.update_status.configure(text="Update check failed · retry")
                    self._error("Could not update app", value)
                elif kind == "update_installer_ready":
                    if self.host is not None:
                        self.host.close()
                    self.root.destroy()
                    return
                elif kind == "update_installer_error":
                    self.update_busy = False
                    self._set_busy(False, "Current app is still running · update did not install")
                    self.update_status.configure(text="Installer failed · retry")
                    self._error("Could not start app update", value)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _open_output(self):
        if not self.last_output:
            return
        try:
            if os.name == "nt":
                os.startfile(self.last_output)
            else:
                subprocess.Popen(["xdg-open", self.last_output], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:
            self._error("Could not open output folder", error)

    def _save_preferences(self):
        try:
            _write_preferences({"base": self.base.get(), "donor": self.donor.get(), "output": self.output.get(),
                                "base_attire": self.base_attire.get(), "donor_attire": self.donor_attire.get(),
                                "blender": self.blender.get(), "fit_clothing": self.fit.get(),
                                "repair_eyelash": self.repair_eyelash.get()})
        except OSError:
            pass

    def _close(self):
        from tkinter import messagebox
        if self.busy:
            if self.process is None:
                messagebox.showinfo("Task is still running", "Keep this window open until the preview or swap preparation finishes.", parent=self.root)
                return
            if not messagebox.askyesno("Close while working?", "A task is still running. Close the app and stop it? Source models are preserved, but unfinished output is a draft.", parent=self.root):
                return
            process = self.process
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                except OSError:
                    pass
        self._save_preferences()
        if self.host is not None:
            self.host.close()
        # Keep worker logs and temporary job results available for diagnosis.
        self.root.destroy()


def self_test() -> dict:
    """Exercise packaged dependencies without creating a native window."""
    import tkinter
    page = resource_path("assets/current-preview.html")
    text = page.read_text(encoding="utf-8")
    renderer = _load_renderer()
    from ui_texture_preview import inspect_preview
    texture_validation = inspect_preview(page)
    result = {"application": APP_NAME, "version": APP_VERSION, "tk_version": tkinter.TkVersion,
              "preview": str(page), "preview_bytes": page.stat().st_size,
              "preview_sha256": hashlib.sha256(page.read_bytes()).hexdigest(),
              "viewer_controls": {name: name in text for name in ("Standing", "Arms up", "Flex", "Open mouth", "Ring", "Entrance")},
              "renderer_build": callable(getattr(renderer, "build", None)),
              "worker_present": resource_path("assets/helpers/swap_worker.py").is_file(),
              "addon_present": resource_path("assets/WWE2K_MDL").is_dir(),
              "texture_validation": texture_validation,
              "physics_status": "unverified/static_draft"}
    result["success"] = all(result["viewer_controls"].values()) and all(result[k] for k in ("renderer_build", "worker_present", "addon_present")) and texture_validation["payload_valid"]
    return result


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    launch_health = None
    if len(arguments) == 3 and arguments[0] == "--updated-launch":
        launch_health = arguments[1:]
        arguments = []
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--self-test", action="store_true", help="check bundled resources without opening a window")
    parser.add_argument("--build-preview", nargs=3, metavar=("CHARACTER", "ATTIRE", "OUTPUT"), help="build using the original preview engine")
    parser.add_argument("--report", type=Path, help="write the command result as JSON")
    args = parser.parse_args(arguments)
    if args.self_test or args.build_preview:
        try:
            if args.self_test:
                result = self_test()
            else:
                character, attire, output = args.build_preview
                result = build_preview(character, attire, Path(output))
                result = dict(result, success=result.get("texture_validation", {}).get("payload_valid", False),
                              physics_status="unverified/static_draft")
            code = 0 if result.get("success", True) else 1
        except Exception as error:
            result = {"success": False, "error": str(error), "traceback": traceback.format_exc()}
            code = 1
        rendered = json.dumps(result, indent=2, default=str)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered, encoding="utf-8")
        if sys.stdout is not None:
            print(rendered)
        return code
    import tkinter as tk
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    AttireSwapperApp(root)
    if launch_health:
        from app_updater import write_launch_health
        root.after_idle(lambda: write_launch_health(*launch_health, APP_VERSION))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
