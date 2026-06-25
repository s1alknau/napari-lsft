"""
napari widget for LSFT reconstruction.

Provides a Qt-based GUI for loading rotational light-sheet data,
configuring reconstruction parameters, and visualizing the result.
"""

import traceback
from pathlib import Path
from typing import Optional

import napari
import numpy as np
from napari.layers import Image
from napari.utils.notifications import show_info
from qtpy.QtCore import QThread, Signal, Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ._reconstruction import reconstruct_volume, estimate_center_of_rotation


# ---------------------------------------------------------------------------
# Background worker thread
# ---------------------------------------------------------------------------

class ReconstructionWorker(QThread):
    """Run reconstruction in a background thread."""

    progress = Signal(int, int)  # current, total
    finished = Signal(np.ndarray)
    error = Signal(str)

    def __init__(self, data, params):
        super().__init__()
        self.data = data
        self.params = params

    def run(self):
        try:
            volume = reconstruct_volume(
                self.data,
                progress_callback=self._on_progress,
                **self.params,
            )
            self.finished.emit(volume)
        except Exception as e:
            self.error.emit(f"{e}\n{traceback.format_exc()}")

    def _on_progress(self, current, total):
        self.progress.emit(current, total)


# ---------------------------------------------------------------------------
# Main widget
# ---------------------------------------------------------------------------

class LSFTReconstructionWidget(QWidget):
    """
    napari widget for Light Sheet Fluorescence Tomography reconstruction.

    Reconstructs 3D volumes from rotational light-sheet data using
    polar-to-Cartesian interpolation.
    """

    def __init__(self, napari_viewer: napari.Viewer):
        super().__init__()
        self.viewer = napari_viewer
        self._worker: Optional[ReconstructionWorker] = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)

        # ---- Title ----
        title = QLabel("<b>LSFT Reconstruction</b>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(
            "<small>Polar → Cartesian reconstruction for<br>"
            "rotational light-sheet tomography</small>"
        )
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        # ---- Data source ----
        grp_data = QGroupBox("Data")
        form_data = QFormLayout()
        grp_data.setLayout(form_data)

        self.combo_layer = QComboBox()
        self._refresh_layers()
        self.viewer.layers.events.inserted.connect(self._refresh_layers)
        self.viewer.layers.events.removed.connect(self._refresh_layers)
        form_data.addRow("Input layer:", self.combo_layer)

        btn_load = QPushButton("Load file...")
        btn_load.clicked.connect(self._load_file)
        form_data.addRow(btn_load)

        layout.addWidget(grp_data)

        # ---- Geometry ----
        grp_geom = QGroupBox("Geometry")
        form_geom = QFormLayout()
        grp_geom.setLayout(form_geom)

        self.spin_angle_start = QDoubleSpinBox()
        self.spin_angle_start.setRange(-360, 360)
        self.spin_angle_start.setValue(0.0)
        self.spin_angle_start.setSuffix("°")
        form_geom.addRow("Angle start:", self.spin_angle_start)

        self.spin_angle_stop = QDoubleSpinBox()
        self.spin_angle_stop.setRange(0, 720)
        self.spin_angle_stop.setValue(180.0)
        self.spin_angle_stop.setSuffix("°")
        form_geom.addRow("Angle stop:", self.spin_angle_stop)

        self.spin_center_offset = QDoubleSpinBox()
        self.spin_center_offset.setRange(-500, 500)
        self.spin_center_offset.setValue(0.0)
        self.spin_center_offset.setDecimals(1)
        self.spin_center_offset.setSuffix(" px")
        form_geom.addRow("Center offset:", self.spin_center_offset)

        self.chk_auto_center = QCheckBox("Auto-detect center of rotation")
        self.chk_auto_center.setChecked(True)
        form_geom.addRow(self.chk_auto_center)

        layout.addWidget(grp_geom)

        # ---- Reconstruction ----
        grp_recon = QGroupBox("Reconstruction")
        form_recon = QFormLayout()
        grp_recon.setLayout(form_recon)

        self.spin_output_size = QSpinBox()
        self.spin_output_size.setRange(32, 4096)
        self.spin_output_size.setValue(0)
        self.spin_output_size.setSpecialValueText("auto")
        form_recon.addRow("Output size:", self.spin_output_size)

        self.spin_downsample = QDoubleSpinBox()
        self.spin_downsample.setRange(0.05, 1.0)
        self.spin_downsample.setValue(1.0)
        self.spin_downsample.setSingleStep(0.1)
        form_recon.addRow("Downsample:", self.spin_downsample)

        self.combo_interp = QComboBox()
        self.combo_interp.addItems(["Linear (1)", "Cubic (3)"])
        self.combo_interp.setCurrentIndex(1)
        form_recon.addRow("Interpolation:", self.combo_interp)

        self.spin_workers = QSpinBox()
        self.spin_workers.setRange(1, 32)
        self.spin_workers.setValue(4)
        form_recon.addRow("Threads:", self.spin_workers)

        layout.addWidget(grp_recon)

        # ---- Filtering ----
        grp_filter = QGroupBox("Filtering")
        form_filter = QFormLayout()
        grp_filter.setLayout(form_filter)

        self.spin_pre_sigma = QDoubleSpinBox()
        self.spin_pre_sigma.setRange(0, 10)
        self.spin_pre_sigma.setValue(0.0)
        self.spin_pre_sigma.setSingleStep(0.5)
        form_filter.addRow("Pre-filter σ:", self.spin_pre_sigma)

        self.spin_post_sigma = QDoubleSpinBox()
        self.spin_post_sigma.setRange(0, 10)
        self.spin_post_sigma.setValue(0.0)
        self.spin_post_sigma.setSingleStep(0.5)
        form_filter.addRow("Post-filter σ:", self.spin_post_sigma)

        layout.addWidget(grp_filter)

        # ---- Actions ----
        grp_actions = QGroupBox("Actions")
        vbox_actions = QVBoxLayout()
        grp_actions.setLayout(vbox_actions)

        self.btn_estimate_cor = QPushButton("Estimate Center of Rotation")
        self.btn_estimate_cor.clicked.connect(self._estimate_cor)
        vbox_actions.addWidget(self.btn_estimate_cor)

        self.btn_reconstruct = QPushButton("▶  Reconstruct")
        self.btn_reconstruct.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 8px; }"
        )
        self.btn_reconstruct.clicked.connect(self._start_reconstruction)
        vbox_actions.addWidget(self.btn_reconstruct)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        vbox_actions.addWidget(self.progress_bar)

        layout.addWidget(grp_actions)

        # ---- Export ----
        grp_export = QGroupBox("Export")
        hbox_export = QHBoxLayout()
        grp_export.setLayout(hbox_export)

        btn_tiff = QPushButton("Save TIFF")
        btn_tiff.clicked.connect(lambda: self._save_volume("tiff"))
        hbox_export.addWidget(btn_tiff)

        btn_hdf5 = QPushButton("Save HDF5")
        btn_hdf5.clicked.connect(lambda: self._save_volume("hdf5"))
        hbox_export.addWidget(btn_hdf5)

        layout.addWidget(grp_export)

        layout.addStretch()

    # ---- Layer management ----

    def _refresh_layers(self, *_args):
        current = self.combo_layer.currentText()
        self.combo_layer.clear()
        for layer in self.viewer.layers:
            if isinstance(layer, Image) and layer.data.ndim >= 3:
                self.combo_layer.addItem(layer.name)
        idx = self.combo_layer.findText(current)
        if idx >= 0:
            self.combo_layer.setCurrentIndex(idx)

    def _get_input_data(self) -> Optional[np.ndarray]:
        name = self.combo_layer.currentText()
        if not name:
            show_info("No input layer selected.")
            return None
        layer = self.viewer.layers[name]
        data = np.asarray(layer.data, dtype=np.float32)
        if data.ndim < 3:
            show_info("Input must be a 3D stack (angles, X, Y).")
            return None
        return data

    # ---- File loading ----

    def _load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open rotational light-sheet data",
            "",
            "All supported (*.tif *.tiff *.h5 *.hdf5 *.zarr);;"
            "TIFF (*.tif *.tiff);;"
            "HDF5 (*.h5 *.hdf5);;"
            "Zarr (*.zarr)",
        )
        if path:
            self.viewer.open(path, plugin="napari-lsft")

    # ---- Center of rotation ----

    def _estimate_cor(self):
        data = self._get_input_data()
        if data is None:
            return
        offset = estimate_center_of_rotation(data)
        self.spin_center_offset.setValue(offset)
        show_info(f"Estimated center offset: {offset:.1f} px")

    # ---- Reconstruction ----

    def _start_reconstruction(self):
        if self._worker is not None and self._worker.isRunning():
            show_info("Reconstruction already running.")
            return

        data = self._get_input_data()
        if data is None:
            return

        interp_order = 1 if self.combo_interp.currentIndex() == 0 else 3
        output_size = self.spin_output_size.value()
        if output_size == 0:
            output_size = None  # auto

        params = dict(
            angle_start=self.spin_angle_start.value(),
            angle_stop=self.spin_angle_stop.value(),
            center_offset=self.spin_center_offset.value(),
            auto_center=self.chk_auto_center.isChecked(),
            output_size=output_size,
            downsample=self.spin_downsample.value(),
            interpolation_order=interp_order,
            pre_filter_sigma=self.spin_pre_sigma.value(),
            post_filter_sigma=self.spin_post_sigma.value(),
            n_workers=self.spin_workers.value(),
        )

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.btn_reconstruct.setEnabled(False)

        self._worker = ReconstructionWorker(data, params)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, current, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    def _on_finished(self, volume):
        self.progress_bar.setVisible(False)
        self.btn_reconstruct.setEnabled(True)

        # Add to viewer as 3D volume
        layer = self.viewer.add_image(
            volume,
            name="LSFT Reconstruction",
            rendering="mip",
            colormap="magma",
        )

        # Switch to 3D view
        if self.viewer.dims.ndisplay != 3:
            self.viewer.dims.ndisplay = 3

        show_info(
            f"Reconstruction complete: {volume.shape} "
            f"(X={volume.shape[0]}, Y={volume.shape[1]}, Z={volume.shape[2]})"
        )

    def _on_error(self, msg):
        self.progress_bar.setVisible(False)
        self.btn_reconstruct.setEnabled(True)
        show_info(f"Reconstruction error: {msg}")

    # ---- Export ----

    def _save_volume(self, fmt: str):
        # Find the reconstruction layer
        recon_layer = None
        for layer in self.viewer.layers:
            if "LSFT" in layer.name or "Reconstruction" in layer.name:
                recon_layer = layer
                break

        if recon_layer is None:
            show_info("No reconstruction found. Run reconstruction first.")
            return

        if fmt == "tiff":
            path, _ = QFileDialog.getSaveFileName(
                self, "Save TIFF", "", "TIFF (*.tif *.tiff)"
            )
            if path:
                import tifffile
                tifffile.imwrite(
                    path,
                    recon_layer.data.astype(np.float32),
                    imagej=True,
                    metadata={"spacing": 1, "unit": "um", "axes": "ZYX"},
                )
                show_info(f"Saved to {path}")

        elif fmt == "hdf5":
            path, _ = QFileDialog.getSaveFileName(
                self, "Save HDF5", "", "HDF5 (*.h5 *.hdf5)"
            )
            if path:
                import h5py
                with h5py.File(path, "w") as f:
                    f.create_dataset(
                        "reconstruction",
                        data=recon_layer.data.astype(np.float32),
                        compression="gzip",
                    )
                    f.attrs["description"] = "LSFT polar-to-Cartesian reconstruction"
                    f.attrs["shape_order"] = "X, Y_sample, Z_sample"
                show_info(f"Saved to {path}")
