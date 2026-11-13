"""Workflow regressions using real child processes and headless event handling.

Run: python -m unittest discover -s tests -p test_gui_workflow.py -v
No renderer, game files or Tk display is required.
"""
from pathlib import Path
import os
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Source"))
import attire_swapper_gui as gui


class Control:
    def __init__(self):
        self.value = ""
        self.disabled = False
        self.options = {}

    def set(self, value):
        self.value = value

    def state(self, value):
        self.disabled = "disabled" in value

    def configure(self, **values):
        self.options.update(values)

    def stop(self):
        pass


def headless_app():
    app = object.__new__(gui.AttireSwapperApp)
    app.events = queue.Queue()
    app.busy = True
    app.process = None
    app.process_lock = threading.Lock()
    app.cancel_requested = threading.Event()
    app.cancel_busy = False
    app.task_kind = None
    app.task_token = None
    app.task_failed = False
    app.last_result = {"output_root": "previous-draft"}
    app.last_output = "previous-draft"
    for key in ("status", "progress", "base_combo", "donor_combo", "result_button",
                "output_button", "cancel_button", "update_status"):
        setattr(app, key, Control())
    app.controls = [app.result_button, app.output_button, app.cancel_button]
    app.root = type("Root", (), {"after": lambda *args: None})()
    app.lines, app.errors = [], []
    app._log = app.lines.append
    app._error = lambda title, message: app.errors.append((title, message))
    return app


class BlenderSelectionTests(unittest.TestCase):
    def test_supported_installs_win_over_path_and_newest(self):
        with tempfile.TemporaryDirectory() as temporary:
            foundation = Path(temporary) / "Blender Foundation"
            paths = {}
            for version in ("4.3", "4.4", "5.2"):
                path = foundation / ("Blender " + version) / "blender.exe"
                path.parent.mkdir(parents=True)
                path.touch()
                paths[version] = path
            with patch.dict(os.environ, {"ProgramFiles": temporary}, clear=True), \
                    patch.object(gui.shutil, "which", return_value=str(paths["5.2"])):
                self.assertEqual(gui.discover_blender(), str(paths["4.4"]))
            paths["4.4"].unlink()
            with patch.dict(os.environ, {"ProgramFiles": temporary}, clear=True), \
                    patch.object(gui.shutil, "which", return_value=str(paths["5.2"])):
                self.assertEqual(gui.discover_blender(), str(paths["4.3"]))

    def test_unversioned_path_remains_available_as_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "blender"
            executable.touch()
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(gui.shutil, "which", return_value=str(executable)):
                self.assertEqual(gui.discover_blender(), str(executable))

    def test_version_is_read_from_executable_output(self):
        process = gui.spawn_blender([sys.executable, "-c", "print('Blender 4.4.3')"])
        with patch.object(gui, "spawn_blender", return_value=process):
            self.assertEqual(gui.probe_blender_version("chosen-executable"), (4, 4, 3))


class WorkflowStatusTests(unittest.TestCase):
    def test_failure_after_prior_success_does_not_say_draft_created(self):
        app = headless_app()
        def fail():
            raise RuntimeError("export failed")
        thread = app._thread(fail, kind="swap", completion="Ready · draft created")
        thread.join(2)
        app._poll()
        self.assertFalse(app.busy)
        self.assertIn("failed", app.status.value)
        self.assertNotIn("draft created", app.status.value)
        self.assertEqual(app.last_output, "previous-draft")
        self.assertFalse(app.result_button.disabled)
        self.assertEqual(len(app.errors), 1)

    def test_preview_completion_uses_current_action(self):
        app = headless_app()
        thread = app._thread(lambda: None, kind="preview", completion="Ready · 3D preview created.")
        thread.join(2)
        app._poll()
        self.assertEqual(app.status.value, "Ready · 3D preview created.")
        self.assertTrue(app.cancel_button.disabled)

    def test_preview_launch_failure_survives_idle_event(self):
        app = headless_app()
        def fail_open(page):
            raise OSError("browser unavailable")
        app._view_page = fail_open
        app.events.put(("preview", "example.html"))
        app.events.put(("idle", "Ready · 3D preview created."))
        app._poll()
        self.assertIn("failed", app.status.value)

    def test_late_cancellation_cannot_publish_queued_success(self):
        app = headless_app()
        app.task_kind = "swap"
        app.cancel_requested.set()
        app.events.put(("result", {"output_root": "new-draft"}))
        app.events.put(("idle", "Ready · draft created"))
        app._poll()
        self.assertEqual(app.last_output, "previous-draft")
        self.assertIn("cancelled", app.status.value)

    def test_update_error_preserves_running_model_status(self):
        app = headless_app()
        app.task_kind = "swap"
        app.status.set("Exporting attire")
        app.update_busy = True
        app.events.put(("update_error", "network unavailable"))
        app._poll()
        self.assertEqual(app.status.value, "Exporting attire")
        self.assertTrue(app.busy)
        self.assertFalse(app.update_busy)

    def test_cancel_does_not_apply_to_preview(self):
        app = headless_app()
        app.task_kind = "preview"
        app._cancel_swap()
        self.assertFalse(app.cancel_requested.is_set())

    def test_delayed_cancel_uses_captured_process_not_next_worker(self):
        app = headless_app()
        app.task_kind = "swap"
        app.task_token = object()
        old_process, next_process = object(), object()
        app.process = old_process
        deferred = []
        class DeferredThread:
            def __init__(self, target, **kwargs):
                deferred.append(target)
            def start(self):
                pass
        with patch.object(gui.threading, "Thread", DeferredThread), \
                patch.object(gui, "stop_blender") as stop:
            app._cancel_swap()
            app.process = next_process
            app.task_token = object()
            deferred[0]()
            stop.assert_called_once_with(old_process)

    def test_old_cancel_error_does_not_change_next_task(self):
        app = headless_app()
        app.task_kind = "swap"
        app.task_token = object()
        app.status.set("New worker exporting")
        app.events.put(("cancel_error", (object(), "old stop failed")))
        app._poll()
        self.assertEqual(app.status.value, "New worker exporting")
        self.assertTrue(app.busy)

    def test_close_during_preparation_requests_cancel_before_returning(self):
        app = headless_app()
        app.task_kind = "swap"
        messages = SimpleNamespace(showinfo=lambda *args, **kwargs: None)
        with patch.dict(sys.modules, {"tkinter": SimpleNamespace(messagebox=messages)}):
            app._close()
        self.assertTrue(app.cancel_requested.is_set())

    def test_close_stop_failure_keeps_window_and_reports_error(self):
        app = headless_app()
        app.task_kind = "swap"
        app.process = object()
        messages = SimpleNamespace(askyesno=lambda *args, **kwargs: True)
        with patch.dict(sys.modules, {"tkinter": SimpleNamespace(messagebox=messages)}), \
                patch.object(gui, "stop_blender", side_effect=RuntimeError("stop refused")):
            app._close()
        self.assertTrue(app.cancel_requested.is_set())
        self.assertEqual(app.errors, [("Could not stop the swap", unittest.mock.ANY)])


class WorkerCancellationTests(unittest.TestCase):
    def test_cancelled_swap_does_not_start_automatic_checker(self):
        app = headless_app()
        app.cancel_requested.set()
        with patch.object(gui, "spawn_blender") as spawn:
            with self.assertRaises(gui.SwapCancelled):
                app._run_check("blender", "character", "Attire", auto=True)
        spawn.assert_not_called()

    def test_running_automatic_checker_can_be_cancelled(self):
        app = headless_app()
        with tempfile.TemporaryDirectory() as temporary:
            app.temp = root = Path(temporary)
            existing = root / "completed-swap.mcd"
            existing.write_bytes(b"completed exported data")
            command = [sys.executable, "-c", "import time;print('checker ready',flush=True);time.sleep(30)"]
            with patch.object(gui, "check_command", return_value=command):
                thread = app._thread(lambda: app._run_check("blender", "character", "Attire", auto=True),
                                     kind="swap")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    logs = list(root.glob("check-*/blender.log"))
                    if logs and "checker ready" in logs[0].read_text():
                        break
                    time.sleep(.02)
                else:
                    if app.process is not None:
                        gui.stop_blender(app.process)
                    self.fail("automatic checker did not start")
                app._cancel_swap()
                thread.join(5)
                self.assertFalse(thread.is_alive())
                app._poll()
            self.assertEqual(existing.read_bytes(), b"completed exported data")
            self.assertIsNone(app.process)
            self.assertIn("cancelled", app.status.value)
            self.assertFalse(app.errors)

    def test_pre_spawn_cancel_does_not_launch_worker(self):
        app = headless_app()
        app.cancel_requested.set()
        with patch.object(gui, "spawn_blender") as spawn:
            with self.assertRaises(gui.SwapCancelled):
                app._run_swap("blender", Path("job.json"), Path("result.json"))
        spawn.assert_not_called()

    def test_running_worker_and_its_child_stop_sources_logs_outputs_remain(self):
        app = headless_app()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = root / "job.json"
            job.write_text("{}")
            source = root / "source-model.mcd"
            source.write_bytes(b"source model must remain unchanged")
            old_output = root / "existing-output.mcd"
            old_output.write_bytes(b"existing output")
            sentinel = root / "child-survived"
            worker = root / "worker.py"
            child = "import pathlib,time;time.sleep(1);pathlib.Path(" + repr(str(sentinel)) + ").write_text('alive')"
            worker.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable, '-c', "
                              + repr(child) + "])\nprint('worker ready', flush=True)\ntime.sleep(30)\n")
            with patch.object(gui, "probe_blender_version", return_value=(4, 4, 3)), \
                    patch.object(gui, "blender_command", return_value=[sys.executable, str(worker)]):
                thread = app._thread(lambda: app._run_swap("blender", job, root / "result.json"),
                                     kind="swap", completion="Ready · draft created")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if (root / "blender.log").is_file() and "worker ready" in (root / "blender.log").read_text():
                        break
                    time.sleep(.02)
                else:
                    if app.process is not None:
                        gui.stop_blender(app.process)
                    self.fail("real worker did not start")
                app._cancel_swap()
                thread.join(5)
                self.assertFalse(thread.is_alive())
                app._poll()
                time.sleep(1.2)
            self.assertFalse(sentinel.exists(), "worker child survived cancellation")
            self.assertEqual(source.read_bytes(), b"source model must remain unchanged")
            self.assertEqual(old_output.read_bytes(), b"existing output")
            self.assertTrue(job.is_file())
            self.assertIn("worker ready", (root / "blender.log").read_text())
            self.assertIn("cancelled", app.status.value)
            self.assertFalse(app.errors)
            self.assertIsNone(app.process)

    @unittest.skipIf(os.name == "nt", "POSIX owned-group regression; Windows orphan containment is not claimed")
    def test_exited_launcher_and_sigterm_ignoring_child_are_stopped(self):
        app = headless_app()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = root / "job.json"
            job.write_text("{}")
            sentinel = root / "orphan-survived"
            worker = root / "launcher.py"
            child = ("import pathlib,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                     "print('child ready',flush=True);time.sleep(1);pathlib.Path("
                     + repr(str(sentinel)) + ").write_text('alive')")
            worker.write_text("import subprocess,sys\nsubprocess.Popen([sys.executable, '-c', " + repr(child) + "])\n")
            with patch.object(gui, "probe_blender_version", return_value=(4, 4, 3)), \
                    patch.object(gui, "blender_command", return_value=[sys.executable, str(worker)]):
                thread = app._thread(lambda: app._run_swap("blender", job, root / "result.json"), kind="swap")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    process = app.process
                    if (process is not None and process.poll() is not None and (root / "blender.log").is_file()
                            and "child ready" in (root / "blender.log").read_text()):
                        break
                    time.sleep(.02)
                else:
                    if app.process is not None:
                        gui.stop_blender(app.process)
                    self.fail("exited launcher with an active child was not reproduced")
                self.assertTrue(thread.is_alive(), "child must keep the worker stdout pipe open")
                app._cancel_swap()
                thread.join(5)
                self.assertFalse(thread.is_alive())
                app._poll()
                time.sleep(1.2)
            self.assertFalse(sentinel.exists())
            self.assertIn("cancelled", app.status.value)
            self.assertFalse(app.errors)
            self.assertIsNone(app.process)


if __name__ == "__main__":
    unittest.main()
