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
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ._acquire import acquire_stack
from ._camera import make_frame_source
from ._galvo import GalvoNotAvailable, GalvoScanner
from ._hardware import ESP32Controller, ESP32NotAvailable
from ._reconstruction import reconstruct_volume

# The laser lines as wired on this rig, listed by wavelength: (nm, LASERid).
# LASERid is the firmware channel (GPIO 16/17/18/19 for 1/2/3/4) -- it is the
# wiring order, not the spectral order, which is why 405 comes last.
LASER_LINES = ((405, 4), (488, 1), (532, 2), (632, 3))


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
        self.galvo: Optional[GalvoScanner] = None
        self.source = None
        self._worker: Optional[AcquisitionWorker] = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        # The panel is taller than most napari docks, so the whole stack lives
        # inside a scroll area; otherwise the lower groups are simply clipped.
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        self.setLayout(outer)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # Let the dock get narrow without forcing a horizontal scrollbar.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        outer.addWidget(scroll)

        content = QWidget()
        layout = QVBoxLayout()
        content.setLayout(layout)
        scroll.setWidget(content)

        title = QLabel("<b>LSFT Control &amp; Acquisition</b>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        layout.addWidget(self._boards_group())
        layout.addWidget(self._camera_group())
        layout.addWidget(self._illumination_group())
        layout.addWidget(self._sheet_group())
        layout.addWidget(self._rotation_group())
        layout.addWidget(self._acquisition_group())

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        layout.addStretch()
        self._set_hw_enabled(False)
        self.grp_sheet.setEnabled(False)

    # ---- Controller boards ----
    def _boards_group(self):
        """The two microcontrollers, together: they are what has to be live
        before anything else in this panel does something."""
        grp = QGroupBox("Controller boards")
        form = QFormLayout()
        grp.setLayout(form)

        # LSFT board: lasers + capillary rotation.
        self.edit_port = QLineEdit("auto")
        form.addRow("ESP32 port:", self.edit_port)
        self.btn_connect_esp = QPushButton("Connect ESP32")
        self.btn_connect_esp.clicked.connect(self._connect_esp32)
        form.addRow(self.btn_connect_esp)
        self.lbl_esp = QLabel("<i>ESP32: not connected</i>")
        form.addRow(self.lbl_esp)

        # Galvo scanner: a second board on its own port. It has no usable
        # auto-detection -- it enumerates as a bare USB-Serial/JTAG device.
        self.edit_galvo_port = QLineEdit("COM7")
        form.addRow("Galvo port:", self.edit_galvo_port)
        self.btn_connect_galvo = QPushButton("Connect galvo")
        self.btn_connect_galvo.clicked.connect(self._connect_galvo)
        form.addRow(self.btn_connect_galvo)
        self.lbl_galvo = QLabel("<i>Galvo: not connected</i>")
        form.addRow(self.lbl_galvo)
        return grp

    # ---- Camera ----
    def _camera_group(self):
        grp = QGroupBox("Camera")
        form = QFormLayout()
        grp.setLayout(form)

        self.combo_source = QComboBox()
        self.combo_source.addItems(["imswitch-http", "imswitch-stream", "gxipy",
                                    "simulated"])
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

        # Named by wavelength; the firmware channel travels in the item data,
        # so rewiring only means editing LASER_LINES.
        self.combo_laser = QComboBox()
        for nm, channel in LASER_LINES:
            self.combo_laser.addItem(f"{nm} nm  (ch {channel})", channel)
        self.combo_laser.setCurrentIndex(1)  # 488 nm
        form.addRow("Laser line:", self.combo_laser)
        self.spin_laser_val = QSpinBox()
        self.spin_laser_val.setRange(0, 255)
        self.spin_laser_val.setValue(128)
        form.addRow("Laser power (0-255):", self.spin_laser_val)
        row = QHBoxLayout()
        b_on = QPushButton("Laser ON"); b_on.clicked.connect(self._laser_on)
        b_off = QPushButton("Laser OFF"); b_off.clicked.connect(self._laser_off)
        row.addWidget(b_on); row.addWidget(b_off)
        form.addRow(row)

        return grp

    # ---- Light sheet ----
    def _sheet_group(self):
        """Its own group because it is its own board: the sheet follows the
        galvo connection, not the ESP32 one."""
        self.grp_sheet = QGroupBox("Light sheet")
        grp = self.grp_sheet
        form = QFormLayout()
        grp.setLayout(form)

        # Sweep geometry in DAC counts (12 bit, 0-4095). Frequency and
        # amplitude are gone: they described the ESP32's own DAC waveform, and
        # the sheet is now formed by the UC2 galvo board instead.
        self.spin_sheet_center = QSpinBox()
        self.spin_sheet_center.setRange(0, 4095); self.spin_sheet_center.setValue(2048)
        form.addRow("Sheet centre (0-4095):", self.spin_sheet_center)
        self.spin_sheet_width = QSpinBox()
        self.spin_sheet_width.setRange(0, 4095); self.spin_sheet_width.setValue(2048)
        form.addRow("Sheet width:", self.spin_sheet_width)
        self.spin_sheet_step = QSpinBox()
        self.spin_sheet_step.setRange(1, 512); self.spin_sheet_step.setValue(8)
        form.addRow("Sweep step:", self.spin_sheet_step)
        self.spin_sheet_dwell = QSpinBox()
        self.spin_sheet_dwell.setRange(0, 10000); self.spin_sheet_dwell.setValue(10)
        form.addRow("Dwell (us):", self.spin_sheet_dwell)
        self.spin_sheet_parkx = QSpinBox()
        self.spin_sheet_parkx.setRange(0, 4095); self.spin_sheet_parkx.setValue(2048)
        form.addRow("Slow axis park X:", self.spin_sheet_parkx)
        row_g = QHBoxLayout()
        b_g_on = QPushButton("Light sheet ON"); b_g_on.clicked.connect(self._galvo_on)
        b_g_off = QPushButton("Light sheet OFF"); b_g_off.clicked.connect(self._galvo_off)
        row_g.addWidget(b_g_on); row_g.addWidget(b_g_off)
        form.addRow(row_g)

        # Hold the mirrors at fixed DAC codes. This is how you find out whether
        # the galvo really moves: step a value through 0 / 1024 / 2048 / 3072 /
        # 4095 with a meter across a differential output and watch it walk over
        # roughly +/-10 V. No scan engine, no optics involved.
        #
        # X and Y are separate on purpose: setting them to *different* codes is
        # what tells you which physical output is which, since the silkscreen
        # names them L and R (the board descends from a laser-show design).
        self.spin_park_x = QSpinBox()
        self.spin_park_x.setRange(0, 4095)
        self.spin_park_x.setValue(2048)
        self.spin_park_x.setSingleStep(1024)
        form.addRow("Park X (DAC A):", self.spin_park_x)
        self.spin_park_y = QSpinBox()
        self.spin_park_y.setRange(0, 4095)
        self.spin_park_y.setValue(2048)
        self.spin_park_y.setSingleStep(1024)
        form.addRow("Park Y (DAC B):", self.spin_park_y)
        b_park = QPushButton("Hold mirrors at these positions")
        b_park.clicked.connect(self._galvo_park)
        form.addRow(b_park)
        self.lbl_park = QLabel("")
        self.lbl_park.setWordWrap(True)
        form.addRow(self.lbl_park)
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
    def _connect_galvo(self):
        try:
            self.galvo = GalvoScanner(
                port=self.edit_galvo_port.text().strip() or "COM7",
            ).connect()
        except GalvoNotAvailable as e:
            self.galvo = None
            self.lbl_galvo.setText("<i>Galvo: not connected</i>")
            show_info(str(e))
            return
        # connect() already applied the sweep, because opening the port reboots
        # the board and nothing it stores survives that.
        self._apply_sheet()
        self.lbl_galvo.setText(f"<b>Galvo:</b> {self.galvo.identify()}")
        self.grp_sheet.setEnabled(True)

    def _sheet_kwargs(self) -> dict:
        return dict(
            center=self.spin_sheet_center.value(),
            width=self.spin_sheet_width.value(),
            step=self.spin_sheet_step.value(),
            dwell_us=self.spin_sheet_dwell.value(),
            park_x=self.spin_sheet_parkx.value(),
        )

    def _apply_sheet(self) -> bool:
        if self.galvo is None:
            return False
        return self.galvo.configure_sheet(**self._sheet_kwargs())

    def _connect_esp32(self):
        try:
            # Steps per turn and the axis are properties of the rig, not
            # choices: the capillary always hangs on the A axis, and its
            # resolution is whatever one motor step is (see STEPS_PER_TURN).
            self.controller = ESP32Controller(
                serialport=self.edit_port.text().strip() or "auto",
            ).connect()
        except ESP32NotAvailable as e:
            self.controller = None
            self.lbl_esp.setText("<i>ESP32: not connected</i>")
            show_info(str(e))
            return
        state = "connected" if self.controller.is_connected else "MOCK (no hardware)"
        # Name the board that actually answered: with two ESP32s on the bench,
        # "connected" alone hides which one is listening.
        self.lbl_esp.setText(
            f"<b>ESP32: {state}</b><br><small>{self.controller.identify()}</small>"
        )
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

        # The simulated rig is its own rotation controller, so picking it also
        # satisfies the ESP32 half - no hardware needed to exercise the widget.
        if kind == "simulated":
            self.controller = self.source
            self.lbl_esp.setText("<b>ESP32: SIMULATED</b>")
            self._set_hw_enabled(True)

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

    def _laser_channel(self) -> int:
        """Firmware LASERid of the selected line."""
        return int(self.combo_laser.currentData())

    def _laser_on(self):
        if self._guard():
            self.controller.laser_on(self.spin_laser_val.value(),
                                     self._laser_channel())

    def _laser_off(self):
        if self._guard():
            self.controller.laser_off(self._laser_channel())

    def _galvo_guard(self) -> bool:
        # The sheet is no longer on the ESP32: GALVO_ENABLED is off in the LSFT
        # firmware and its DAC pins now drive the rotation stepper, so there is
        # no fallback to offer here.
        if self.galvo is None:
            show_info("Connect the galvo board first (Galvo port).")
            return False
        return True

    def _galvo_on(self):
        if self._galvo_guard():
            self._apply_sheet()
            if not self.galvo.light_sheet_on():
                show_info("Galvo did not acknowledge - is it still scanning a "
                          "long frame? Try again.")

    def _galvo_off(self):
        if self._galvo_guard():
            self.galvo.light_sheet_off()

    @staticmethod
    def _dac_volts(code: int) -> float:
        """Roughly what a differential output should read at this DAC code."""
        return (code / 4095.0 * 20.0) - 10.0

    def _galvo_park(self):
        if not self._galvo_guard():
            return
        x, y = self.spin_park_x.value(), self.spin_park_y.value()
        if self.galvo.park(x=x, y=y):
            self.lbl_park.setText(
                f"<i>holding X={x} (~{self._dac_volts(x):+.1f} V) and "
                f"Y={y} (~{self._dac_volts(y):+.1f} V). Measure across + and "
                f"&minus; of one output header, not against ground.</i>")
        else:
            self.lbl_park.setText("<i>no acknowledgement &mdash; try again</i>")

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
        if self.chk_auto_galvo.isChecked() and self.galvo is None:
            # Without a galvo board there is nothing to switch: falling back to
            # the ESP32 would call /dac_act, which its firmware only
            # acknowledges. Say so rather than record a stack in the dark.
            show_info("Auto light sheet is on but no galvo board is connected "
                      "- the sheet will not be switched. Connect it, or untick "
                      "the box and drive the sweep yourself.")

        params = dict(
            n_angles=self.spin_nangles.value(),
            angle_start=self.spin_astart.value(),
            angle_stop=self.spin_astop.value(),
            laser_value=self.spin_laser_val.value(),
            laser_channel=self._laser_channel(),
            auto_galvo=self.chk_auto_galvo.isChecked(),
            sheet=self.galvo,
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
