"""
napari widget for LSFT hardware control + acquisition.

This is the "control in the plugin" front-end: it drives the UC2 ESP32 directly
(laser / LED / galvo / rotation) and pulls camera frames from a swappable
source (ImSwitch over HTTP/stream, or a Daheng camera via gxipy). ImSwitch, if
used, only needs to provide the camera (see acquisition/LSFT_camera.json).

The acquisition runs in a background thread and its result (raw stack, and
optionally the reconstructed 3D volume) is shown in the napari viewer.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from napari.utils.notifications import show_info
from qtpy.QtCore import Qt, QThread, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ._acquire import acquire_stack
from ._camera import make_frame_source
from ._hardware import ESP32Controller, ESP32NotAvailable
from ._reconstruction import reconstruct_volume


# --------------------------------------------------------------------------- #
# Background acquisition worker
# --------------------------------------------------------------------------- #

class AcquisitionWorker(QThread):
    progress = Signal(int, int)
    finished = Signal(object)  # np.ndarray stack (or path str)
    error = Signal(str)

    def __init__(self, controller, source, params):
        super().__init__()
        self.controller = controller
        self.source = source
        self.params = params

    def run(self):
        try:
            result = acquire_stack(
                self.controller, self.source,
                progress_callback=lambda i, n: self.progress.emit(i, n),
                **self.params,
            )
            self.finished.emit(result)
        except Exception as e:  # noqa: BLE001
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")


# --------------------------------------------------------------------------- #
# Main control widget
# --------------------------------------------------------------------------- #

class LSFTControlWidget(QWidget):
    """LSFT hardware control + acquisition for napari."""

    def __init__(self, napari_viewer):
        super().__init__()
        self.viewer = napari_viewer
        self.controller: Optional[ESP32Controller] = None
        self.source = None
        self._worker: Optional[AcquisitionWorker] = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)

        title = QLabel("<b>LSFT Control &amp; Acquisition</b>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        layout.addWidget(self._connection_group())
        layout.addWidget(self._illumination_group())
        layout.addWidget(self._rotation_group())
        layout.addWidget(self._acquisition_group())

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        layout.addStretch()
        self._set_hw_enabled(False)

    # ---- Connection ----
    def _connection_group(self):
        grp = QGroupBox("Connection")
        form = QFormLayout()
        grp.setLayout(form)

        # ESP32
        self.edit_port = QLineEdit("auto")
        form.addRow("ESP32 port:", self.edit_port)
        self.spin_steps_per_turn = QSpinBox()
        self.spin_steps_per_turn.setRange(1, 1_000_000)
        self.spin_steps_per_turn.setValue(3200)
        form.addRow("Steps/turn:", self.spin_steps_per_turn)
        self.btn_connect_esp = QPushButton("Connect ESP32")
        self.btn_connect_esp.clicked.connect(self._connect_esp32)
        form.addRow(self.btn_connect_esp)
        self.lbl_esp = QLabel("<i>ESP32: not connected</i>")
        form.addRow(self.lbl_esp)

        # Camera
        self.combo_source = QComboBox()
        self.combo_source.addItems(["imswitch-http", "imswitch-stream", "gxipy"])
        form.addRow("Camera source:", self.combo_source)
        self.edit_cam_host = QLineEdit("127.0.0.1")
        form.addRow("ImSwitch host:", self.edit_cam_host)
        self.spin_cam_port = QSpinBox()
        self.spin_cam_port.setRange(1, 65535)
        self.spin_cam_port.setValue(8001)
        form.addRow("ImSwitch port:", self.spin_cam_port)
        self.btn_connect_cam = QPushButton("Connect camera")
        self.btn_connect_cam.clicked.connect(self._connect_camera)
        form.addRow(self.btn_connect_cam)
        self.lbl_cam = QLabel("<i>Camera: not connected</i>")
        form.addRow(self.lbl_cam)
        return grp

    # ---- Illumination ----
    def _illumination_group(self):
        grp = QGroupBox("Illumination")
        form = QFormLayout()
        grp.setLayout(form)

        self.spin_laser_ch = QSpinBox()
        self.spin_laser_ch.setRange(1, 4)
        self.spin_laser_ch.setValue(1)
        form.addRow("Laser channel:", self.spin_laser_ch)
        self.spin_laser_val = QSpinBox()
        self.spin_laser_val.setRange(0, 255)
        self.spin_laser_val.setValue(128)
        form.addRow("Laser power (0-255):", self.spin_laser_val)
        row = QHBoxLayout()
        b_on = QPushButton("Laser ON"); b_on.clicked.connect(self._laser_on)
        b_off = QPushButton("Laser OFF"); b_off.clicked.connect(self._laser_off)
        row.addWidget(b_on); row.addWidget(b_off)
        form.addRow(row)

        row_led = QHBoxLayout()
        b_led_on = QPushButton("LED ON"); b_led_on.clicked.connect(self._led_on)
        b_led_off = QPushButton("LED OFF"); b_led_off.clicked.connect(self._led_off)
        row_led.addWidget(b_led_on); row_led.addWidget(b_led_off)
        form.addRow(row_led)

        self.spin_galvo_freq = QDoubleSpinBox()
        self.spin_galvo_freq.setRange(0, 1000); self.spin_galvo_freq.setValue(10)
        form.addRow("Galvo freq (Hz):", self.spin_galvo_freq)
        self.spin_galvo_amp = QDoubleSpinBox()
        self.spin_galvo_amp.setRange(0, 10); self.spin_galvo_amp.setValue(1)
        self.spin_galvo_amp.setSingleStep(0.1)
        form.addRow("Galvo amplitude:", self.spin_galvo_amp)
        row_g = QHBoxLayout()
        b_g_on = QPushButton("Light sheet ON"); b_g_on.clicked.connect(self._galvo_on)
        b_g_off = QPushButton("Light sheet OFF"); b_g_off.clicked.connect(self._galvo_off)
        row_g.addWidget(b_g_on); row_g.addWidget(b_g_off)
        form.addRow(row_g)
        return grp

    # ---- Rotation ----
    def _rotation_group(self):
        grp = QGroupBox("Rotation (A axis)")
        form = QFormLayout()
        grp.setLayout(form)

        self.spin_jog = QDoubleSpinBox()
        self.spin_jog.setRange(-360, 360); self.spin_jog.setValue(10)
        self.spin_jog.setSuffix(" deg")
        form.addRow("Jog step:", self.spin_jog)
        row = QHBoxLayout()
        b_minus = QPushButton("- Rotate"); b_minus.clicked.connect(lambda: self._rotate(-1))
        b_plus = QPushButton("Rotate +"); b_plus.clicked.connect(lambda: self._rotate(+1))
        row.addWidget(b_minus); row.addWidget(b_plus)
        form.addRow(row)
        row2 = QHBoxLayout()
        b_zero = QPushButton("Set zero"); b_zero.clicked.connect(self._zero)
        b_stop = QPushButton("Stop"); b_stop.clicked.connect(self._stop)
        b_pos = QPushButton("Read pos"); b_pos.clicked.connect(self._read_pos)
        row2.addWidget(b_zero); row2.addWidget(b_stop); row2.addWidget(b_pos)
        form.addRow(row2)
        self.lbl_pos = QLabel("position: -")
        form.addRow(self.lbl_pos)
        return grp

    # ---- Acquisition ----
    def _acquisition_group(self):
        grp = QGroupBox("Acquisition")
        form = QFormLayout()
        grp.setLayout(form)

        self.spin_nangles = QSpinBox()
        self.spin_nangles.setRange(2, 100000); self.spin_nangles.setValue(180)
        form.addRow("N angles:", self.spin_nangles)
        self.spin_astart = QDoubleSpinBox()
        self.spin_astart.setRange(-360, 360); self.spin_astart.setValue(0)
        form.addRow("Angle start:", self.spin_astart)
        self.spin_astop = QDoubleSpinBox()
        self.spin_astop.setRange(0, 720); self.spin_astop.setValue(180)
        form.addRow("Angle stop:", self.spin_astop)
        self.spin_settle = QDoubleSpinBox()
        self.spin_settle.setRange(0, 5); self.spin_settle.setValue(0.05)
        self.spin_settle.setSingleStep(0.01)
        form.addRow("Settle (s):", self.spin_settle)
        self.chk_auto_galvo = QCheckBox("Auto light sheet during scan")
        self.chk_auto_galvo.setChecked(True)
        form.addRow(self.chk_auto_galvo)

        row_out = QHBoxLayout()
        self.edit_out = QLineEdit("")
        self.edit_out.setPlaceholderText("raw stack .h5 / .zarr (optional)")
        b_browse = QPushButton("...")
        b_browse.clicked.connect(self._browse_out)
        row_out.addWidget(self.edit_out); row_out.addWidget(b_browse)
        form.addRow("Save raw to:", row_out)

        self.chk_reconstruct = QCheckBox("Reconstruct 3D volume after scan")
        self.chk_reconstruct.setChecked(True)
        form.addRow(self.chk_reconstruct)

        self.btn_acquire = QPushButton("▶  Acquire")
        self.btn_acquire.setStyleSheet("QPushButton { font-weight: bold; padding: 8px; }")
        self.btn_acquire.clicked.connect(self._start_acquire)
        form.addRow(self.btn_acquire)
        return grp

    # ------------------------------------------------------------------ #
    # Connection handlers
    # ------------------------------------------------------------------ #
    def _connect_esp32(self):
        try:
            self.controller = ESP32Controller(
                serialport=self.edit_port.text().strip() or "auto",
                steps_per_turn=self.spin_steps_per_turn.value(),
            ).connect()
        except ESP32NotAvailable as e:
            self.controller = None
            self.lbl_esp.setText("<i>ESP32: not connected</i>")
            show_info(str(e))
            return
        state = "connected" if self.controller.is_connected else "MOCK (no hardware)"
        self.lbl_esp.setText(f"<b>ESP32: {state}</b>")
        self._set_hw_enabled(True)

    def _connect_camera(self):
        kind = self.combo_source.currentText()
        kwargs = {}
        if kind.startswith("imswitch"):
            kwargs = dict(host=self.edit_cam_host.text().strip(),
                          port=self.spin_cam_port.value())
        try:
            self.source = make_frame_source(kind, **kwargs).open()
        except Exception as e:  # noqa: BLE001
            self.source = None
            self.lbl_cam.setText("<i>Camera: not connected</i>")
            show_info(f"Camera connect failed: {e}")
            return
        self.lbl_cam.setText(f"<b>Camera: {kind} connected</b>")

    def _set_hw_enabled(self, enabled: bool):
        # illumination/rotation need the ESP32; acquisition needs both (checked at run)
        for grp in self.findChildren(QGroupBox):
            if grp.title() in ("Illumination", "Rotation (A axis)"):
                grp.setEnabled(enabled)

    # ------------------------------------------------------------------ #
    # Illumination handlers
    # ------------------------------------------------------------------ #
    def _guard(self):
        if self.controller is None:
            show_info("Connect the ESP32 first.")
            return False
        return True

    def _laser_on(self):
        if self._guard():
            self.controller.laser_on(self.spin_laser_val.value(), self.spin_laser_ch.value())

    def _laser_off(self):
        if self._guard():
            self.controller.laser_off(self.spin_laser_ch.value())

    def _led_on(self):
        if self._guard():
            self.controller.set_led(on=True)

    def _led_off(self):
        if self._guard():
            self.controller.led_off()

    def _galvo_on(self):
        if self._guard():
            self.controller.light_sheet_on(
                frequency=self.spin_galvo_freq.value(),
                amplitude=self.spin_galvo_amp.value())

    def _galvo_off(self):
        if self._guard():
            self.controller.light_sheet_off()

    # ------------------------------------------------------------------ #
    # Rotation handlers
    # ------------------------------------------------------------------ #
    def _rotate(self, sign):
        if self._guard():
            self.controller.rotate(sign * self.spin_jog.value(), blocking=False)

    def _zero(self):
        if self._guard():
            self.controller.zero_rotation()
            self.lbl_pos.setText("position: 0.0 deg")

    def _stop(self):
        if self._guard():
            self.controller.stop(axis=self.controller.rotation_axis)

    def _read_pos(self):
        if self._guard():
            self.lbl_pos.setText(f"position: {self.controller.rotation_position():.2f} deg")

    # ------------------------------------------------------------------ #
    # Acquisition
    # ------------------------------------------------------------------ #
    def _browse_out(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save raw stack", "", "HDF5 (*.h5 *.hdf5);;Zarr (*.zarr)")
        if path:
            self.edit_out.setText(path)

    def _start_acquire(self):
        if self.controller is None or self.source is None:
            show_info("Connect both the ESP32 and the camera first.")
            return
        if self._worker is not None and self._worker.isRunning():
            show_info("Acquisition already running.")
            return

        params = dict(
            n_angles=self.spin_nangles.value(),
            angle_start=self.spin_astart.value(),
            angle_stop=self.spin_astop.value(),
            laser_value=self.spin_laser_val.value(),
            laser_channel=self.spin_laser_ch.value(),
            auto_galvo=self.chk_auto_galvo.isChecked(),
            galvo_freq=self.spin_galvo_freq.value(),
            galvo_amplitude=self.spin_galvo_amp.value(),
            settle=self.spin_settle.value(),
            output=self.edit_out.text().strip() or None,
        )
        self._reconstruct_after = self.chk_reconstruct.isChecked()
        self._acq_params = params

        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(params["n_angles"])
        self.progress_bar.setValue(0)
        self.btn_acquire.setEnabled(False)

        self._worker = AcquisitionWorker(self.controller, self.source, params)
        self._worker.progress.connect(lambda i, n: self.progress_bar.setValue(i))
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, result):
        self.progress_bar.setVisible(False)
        self.btn_acquire.setEnabled(True)

        if not isinstance(result, np.ndarray):
            show_info(f"Acquisition saved to {result}")
            return

        self.viewer.add_image(result, name="LSFT raw stack")

        if self._reconstruct_after:
            show_info("Reconstructing 3D volume...")
            volume = reconstruct_volume(
                result,
                angle_start=self._acq_params["angle_start"],
                angle_stop=self._acq_params["angle_stop"],
                auto_center=True,
            )
            self.viewer.add_image(volume, name="LSFT Reconstruction",
                                  rendering="mip", colormap="magma")
            if self.viewer.dims.ndisplay != 3:
                self.viewer.dims.ndisplay = 3
            show_info(f"Done: raw {result.shape} -> volume {volume.shape}")
        else:
            show_info(f"Acquired raw stack {result.shape}")

    def _on_error(self, msg):
        self.progress_bar.setVisible(False)
        self.btn_acquire.setEnabled(True)
        show_info(f"Acquisition error: {msg}")
