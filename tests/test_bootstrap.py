"""Unit tests for the extracted Qt bootstrap flow."""

import unittest
from unittest.mock import patch

from ui_qt import bootstrap


class _FakeLoadingScreen:
    def __init__(self, order=None):
        self.destroyed = False
        self.statuses = []
        self.progress = []
        self.shown = False
        self.order = order

    def show(self):
        self.shown = True
        if self.order is not None:
            self.order.append("loading_screen_shown")

    def update_status(self, status):
        self.statuses.append(status)

    def update_progress(self, progress):
        self.progress.append(progress)

    def repaint(self):
        pass

    def destroy(self):
        self.destroyed = True


class _FakeUIController:
    def __init__(self):
        self.main_window = object()
        self.show_main_window_called = False
        self.update_check_scheduled = False
        self.device_info = None
        self.cleaned_up = False

    def show_main_window(self):
        self.show_main_window_called = True

    def set_device_info(self, device_info):
        self.device_info = device_info

    def schedule_startup_update_check(self):
        self.update_check_scheduled = True

    def cleanup(self):
        self.cleaned_up = True


class _FakeQtApplication:
    def __init__(self):
        self.main_window = None
        self.raise_on_run = False

    def run(self, main_window):
        self.main_window = main_window
        if self.raise_on_run:
            raise RuntimeError("boom")
        return 123


class _FakeBackend:
    def __init__(self, device_info="cpu"):
        self.device_info = device_info


class _FakeApplicationController:
    should_raise = False
    instances = []

    def __init__(
        self,
        ui_controller,
        local_backend=None,
        defer_local_backend=False,
    ):
        if self.should_raise:
            raise RuntimeError("controller init failed")
        self.ui_controller = ui_controller
        self.local_backend = local_backend
        self.defer_local_backend = defer_local_backend
        self.cleaned_up = False
        self.main_ui_ready_notified = False
        self.transcription_backends = {"local_whisper": _FakeBackend("cuda")}
        self.__class__.instances.append(self)

    def notify_main_ui_ready(self):
        self.main_ui_ready_notified = True

    def cleanup(self):
        self.cleaned_up = True


class TestBootstrap(unittest.TestCase):
    def setUp(self):
        _FakeApplicationController.instances = []
        _FakeApplicationController.should_raise = False
        bootstrap._SHUTDOWN_REQUESTED = False

    def test_uninstall_shutdown_command_does_not_start_another_instance(self):
        with patch.object(
            bootstrap.sys,
            "argv",
            ["MeetingRecorder.exe", "--shutdown-for-uninstall"],
        ), patch.object(
            bootstrap,
            "request_running_instance_shutdown",
            return_value=True,
        ) as request_shutdown, patch.object(
            bootstrap,
            "acquire_single_instance",
        ) as acquire_instance:
            result = bootstrap.main()

        self.assertEqual(result, 0)
        request_shutdown.assert_called_once_with()
        acquire_instance.assert_not_called()

    @patch("services.settings.is_hf_hub_offline_env_set", return_value=False)
    @patch.object(bootstrap, "run_with_ui_pulse", side_effect=lambda fn: fn())
    @patch.object(bootstrap, "process_qt_events")
    @patch.object(bootstrap, "setup_logging")
    def test_main_runs_startup_flow_and_cleans_up_controller(
        self,
        _mock_setup_logging,
        _mock_process_events,
        _mock_pulse,
        _mock_hf_env,
    ):
        qt_app = _FakeQtApplication()
        ui_controller = _FakeUIController()
        order = []
        loading_screen = _FakeLoadingScreen(order)

        def get_early_runtime_components():
            order.append("early_imports")
            return lambda: qt_app, lambda: loading_screen

        def get_main_ui_component():
            order.append("main_ui_imports")
            return lambda: ui_controller

        def get_application_controller_component():
            order.append("application_runtime_imports")
            return _FakeApplicationController

        _mock_process_events.side_effect = lambda: order.append("process_events")

        with patch.object(
            bootstrap,
            "get_early_runtime_components",
            side_effect=get_early_runtime_components,
        ), patch.object(
            bootstrap,
            "get_main_ui_component",
            side_effect=get_main_ui_component,
        ), patch.object(
            bootstrap,
            "get_application_controller_component",
            side_effect=get_application_controller_component,
        ), patch.object(
            bootstrap,
            "acquire_single_instance",
            return_value=True,
        ), patch.object(
            bootstrap,
            "install_shutdown_listener",
        ) as install_shutdown_listener, patch.object(
            bootstrap,
            "release_shutdown_listener",
        ):
            result = bootstrap.main()

        self.assertEqual(result, 123)
        self.assertTrue(loading_screen.destroyed)
        self.assertTrue(ui_controller.show_main_window_called)
        self.assertTrue(ui_controller.update_check_scheduled)
        self.assertIsNone(ui_controller.device_info)
        self.assertEqual(len(_FakeApplicationController.instances), 1)
        self.assertTrue(
            _FakeApplicationController.instances[0].defer_local_backend
        )
        self.assertTrue(_FakeApplicationController.instances[0].cleaned_up)
        self.assertTrue(_FakeApplicationController.instances[0].main_ui_ready_notified)
        install_shutdown_listener.assert_called_once_with(qt_app)
        self.assertLess(
            order.index("loading_screen_shown"),
            order.index("main_ui_imports"),
        )
        self.assertLess(
            order.index("main_ui_imports"),
            order.index("application_runtime_imports"),
        )

    @patch("services.settings.is_hf_hub_offline_env_set", return_value=False)
    @patch.object(bootstrap, "run_with_ui_pulse", side_effect=lambda fn: fn())
    @patch.object(bootstrap, "process_qt_events")
    @patch.object(bootstrap, "setup_logging")
    def test_main_cleans_up_loading_screen_and_controller_on_run_error(
        self,
        _mock_setup_logging,
        _mock_process_events,
        _mock_pulse,
        _mock_hf_env,
    ):
        qt_app = _FakeQtApplication()
        qt_app.raise_on_run = True
        ui_controller = _FakeUIController()
        loading_screen = _FakeLoadingScreen()

        with patch.object(
            bootstrap,
            "get_early_runtime_components",
            return_value=(lambda: qt_app, lambda: loading_screen),
        ), patch.object(
            bootstrap,
            "get_main_ui_component",
            return_value=(lambda: ui_controller),
        ), patch.object(
            bootstrap,
            "get_application_controller_component",
            return_value=_FakeApplicationController,
        ), patch.object(
            bootstrap,
            "acquire_single_instance",
            return_value=True,
        ), patch.object(
            bootstrap,
            "install_shutdown_listener",
        ), patch.object(
            bootstrap,
            "release_shutdown_listener",
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                bootstrap.main()

        self.assertTrue(loading_screen.destroyed)
        self.assertEqual(len(_FakeApplicationController.instances), 1)
        self.assertTrue(_FakeApplicationController.instances[0].cleaned_up)

    @patch("services.settings.is_hf_hub_offline_env_set", return_value=False)
    @patch.object(bootstrap, "run_with_ui_pulse", side_effect=lambda fn: fn())
    @patch.object(bootstrap, "process_qt_events")
    @patch.object(bootstrap, "setup_logging")
    def test_shutdown_requested_during_startup_skips_event_loop(
        self,
        _mock_setup_logging,
        _mock_process_events,
        _mock_pulse,
        _mock_hf_env,
    ):
        qt_app = _FakeQtApplication()
        ui_controller = _FakeUIController()
        loading_screen = _FakeLoadingScreen()

        def mark_shutdown(_qt_app):
            bootstrap._SHUTDOWN_REQUESTED = True

        with patch.object(
            bootstrap,
            "get_early_runtime_components",
            return_value=(lambda: qt_app, lambda: loading_screen),
        ), patch.object(
            bootstrap,
            "get_main_ui_component",
            return_value=(lambda: ui_controller),
        ), patch.object(
            bootstrap,
            "get_application_controller_component",
            return_value=_FakeApplicationController,
        ), patch.object(
            bootstrap,
            "acquire_single_instance",
            return_value=True,
        ), patch.object(
            bootstrap,
            "install_shutdown_listener",
            side_effect=mark_shutdown,
        ), patch.object(
            bootstrap,
            "release_shutdown_listener",
        ):
            result = bootstrap.main()

        self.assertEqual(result, 0)
        self.assertIsNone(qt_app.main_window)
        self.assertEqual(len(_FakeApplicationController.instances), 1)
        self.assertTrue(_FakeApplicationController.instances[0].cleaned_up)


if __name__ == "__main__":
    unittest.main()
