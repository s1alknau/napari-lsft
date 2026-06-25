"""
LSFT Reconstruction Visualization with PyVista
================================================

Generates a synthetic phantom (cuboid inside narrow glass capillary),
reconstructs a 3D volume via polar→Cartesian interpolation, and
renders the result with PyVista including:
  - Semi-transparent glass capillary
  - Light sheet (XY plane)
  - Laser beam arrows
  - Detection axis
  - Rotation axis + indicator
  - Reconstructed cuboid

Outputs:
  - Lightsheet Fluorescence 3D Volume imaging — Nematostella vectensis_pyvista_perspective.png   (high-res single view)
  - Lightsheet Fluorescence 3D Volume imaging — Nematostella vectensis_pyvista_4views.png        (4 orthogonal views)
  - Lightsheet Fluorescence 3D Volume imaging — Nematostella vectensis_pyvista_anim.gif          (rotating animation)

Requirements:
  pip install pyvista vtk numpy scipy scikit-image imageio

Usage:
  python visualize_reconstruction.py
"""

import sys
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pyvista as pv

# Add parent src to path so we can import the plugin
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from napari_lsft._reconstruction import reconstruct_slice, reconstruct_volume

# =============================================
# Configuration
# =============================================
OUTPUT_DIR = Path(__file__).resolve().parent
N_X = 200           # pixels along capillary
N_Y = 48            # pixels across capillary (diameter)
N_ANGLES = 180      # number of rotation steps
CS_SIZE = 96        # reconstruction output size (YZ)
CAP_INNER = 10      # capillary inner radius
CAP_OUTER = 11.5    # capillary outer radius
CUBOID_HALF = 2.5   # cuboid half-width in YZ
CUBOID_X_FRAC = 0.15  # cuboid extent as fraction of X range
N_ANIM_FRAMES = 120
ANIM_FPS = 20

pv.OFF_SCREEN = True
pv.global_theme.background = [13, 17, 23]
pv.global_theme.font.color = "white"


# =============================================
# 1. Generate synthetic data
# =============================================
def generate_phantom():
    """Create camera data for a cuboid inside a narrow capillary."""
    ys = np.linspace(-15, 15, CS_SIZE)
    zs = np.linspace(-15, 15, CS_SIZE)
    YS, ZS = np.meshgrid(ys, zs)
    R = np.sqrt(YS**2 + ZS**2)

    # Cross-sections
    capillary_wall = np.where(
        (R >= CAP_INNER) & (R <= CAP_OUTER), 0.2, 0.0
    )
    cuboid_yz = np.where(
        (np.abs(YS) < CUBOID_HALF) & (np.abs(ZS) < CUBOID_HALF), 1.0, 0.0
    )

    # Cuboid present only in central portion of X
    x_pos = np.linspace(-1, 1, N_X)
    cuboid_x_mask = np.where(np.abs(x_pos) < CUBOID_X_FRAC, 1.0, 0.0)

    # Simulate camera acquisition
    angles_rad = np.linspace(0, np.pi, N_ANGLES, endpoint=False)
    y_lab = np.linspace(-CAP_OUTER, CAP_OUTER, N_Y)

    print("Generating synthetic camera data...")
    camera_data = np.zeros((N_ANGLES, N_X, N_Y), dtype=np.float32)

    for ix in range(N_X):
        cs = np.clip(capillary_wall + cuboid_yz * cuboid_x_mask[ix], 0, 1)
        for ia, theta in enumerate(angles_rad):
            for jy, yl in enumerate(y_lab):
                y_s = yl * np.cos(theta)
                z_s = yl * np.sin(theta)
                iy = (y_s + 15) / 30 * (CS_SIZE - 1)
                iz = (z_s + 15) / 30 * (CS_SIZE - 1)
                iy_i, iz_i = int(iy), int(iz)
                if 0 <= iy_i < CS_SIZE - 1 and 0 <= iz_i < CS_SIZE - 1:
                    fy, fz = iy - iy_i, iz - iz_i
                    camera_data[ia, ix, jy] = (
                        cs[iz_i, iy_i] * (1 - fy) * (1 - fz)
                        + cs[iz_i, iy_i + 1] * fy * (1 - fz)
                        + cs[iz_i + 1, iy_i] * (1 - fy) * fz
                        + cs[iz_i + 1, iy_i + 1] * fy * fz
                    )

    print(f"  Camera data shape: {camera_data.shape}")
    return camera_data


# =============================================
# 2. Reconstruct
# =============================================
def reconstruct(camera_data):
    """Run polar→Cartesian reconstruction."""
    print("Reconstructing volume...")
    volume = reconstruct_volume(
        camera_data,
        angle_start=0,
        angle_stop=180,
        auto_center=False,
        center_offset=0.0,
        output_size=CS_SIZE,
        n_workers=4,
    )
    print(f"  Volume shape: {volume.shape}")
    return volume


# =============================================
# 3. Build PyVista meshes
# =============================================
def build_meshes(volume):
    """Create all PyVista meshes for visualization."""
    x_extent = 30 * (N_X / CS_SIZE)
    x_half = x_extent / 2
    voxel_x = x_extent / volume.shape[0]
    voxel_yz = 30.0 / CS_SIZE

    meshes = {}

    # --- Glass capillary ---
    meshes["cap_outer"] = pv.Cylinder(
        center=(0, 0, 0),
        direction=(1, 0, 0),
        radius=CAP_OUTER,
        height=x_extent * 0.98,
        resolution=80,
        capping=True,
    )
    meshes["cap_inner"] = pv.Cylinder(
        center=(0, 0, 0),
        direction=(1, 0, 0),
        radius=CAP_INNER,
        height=x_extent * 0.99,
        resolution=80,
        capping=True,
    )

    # --- Light sheet (XY plane at z=0) ---
    meshes["light_sheet"] = pv.Plane(
        center=(0, 0, 0),
        direction=(0, 0, 1),
        i_size=x_extent * 0.92,
        j_size=CAP_INNER * 2.6,
    )

    # --- Laser beam arrows ---
    meshes["laser_arrows"] = []
    for x_arr in np.linspace(-x_half * 0.5, x_half * 0.5, 5):
        arrow = pv.Arrow(
            start=(x_arr, -CAP_INNER * 1.6, 0),
            direction=(0, 1, 0),
            scale=CAP_INNER * 0.5,
            tip_length=0.3,
            tip_radius=0.12,
            shaft_radius=0.04,
        )
        meshes["laser_arrows"].append(arrow)

    # --- Detection arrow (Z from below) ---
    meshes["det_arrow"] = pv.Arrow(
        start=(0, 0, -16),
        direction=(0, 0, 1),
        scale=4,
        tip_length=0.3,
        tip_radius=0.15,
        shaft_radius=0.06,
    )

    # --- Rotation axis ---
    meshes["rot_axis"] = pv.Line(
        (-x_half * 1.15, 0, 0), (x_half * 1.15, 0, 0)
    )

    # --- Rotation ring indicator ---
    ring_theta = np.linspace(0, 2 * np.pi, 100)
    ring_r = CAP_INNER * 1.35
    ring_pts = np.column_stack(
        [
            np.full(100, x_half * 0.85),
            ring_r * np.cos(ring_theta),
            ring_r * np.sin(ring_theta),
        ]
    )
    meshes["rot_ring"] = pv.Spline(ring_pts, n_points=100)
    meshes["rot_arrow"] = pv.Arrow(
        start=(x_half * 0.85, ring_r * np.cos(0.5), ring_r * np.sin(0.5)),
        direction=(0, -np.sin(0.5), np.cos(0.5)),
        scale=2,
        tip_length=0.5,
        tip_radius=0.25,
        shaft_radius=0.01,
    )

    # --- Reconstructed volume → thresholded cuboid ---
    grid = pv.ImageData()
    grid.dimensions = np.array(volume.shape) + 1
    grid.origin = (-x_half, -15, -15)
    grid.spacing = (voxel_x, voxel_yz, voxel_yz)
    grid.cell_data["values"] = volume.flatten(order="F")
    meshes["cuboid"] = grid.threshold(0.45)

    meshes["x_half"] = x_half
    return meshes


# =============================================
# 4. Add all meshes to a plotter
# =============================================
def add_scene(plotter, meshes):
    """Add all meshes to a PyVista plotter."""
    # Glass capillary
    plotter.add_mesh(
        meshes["cap_outer"],
        color="#4499cc",
        opacity=0.1,
        smooth_shading=True,
        specular=0.5,
        specular_power=30,
    )
    plotter.add_mesh(
        meshes["cap_inner"],
        color="#88bbdd",
        opacity=0.05,
        smooth_shading=True,
        style="wireframe",
        line_width=0.5,
    )

    # Light sheet
    plotter.add_mesh(
        meshes["light_sheet"],
        color="#00ff88",
        opacity=0.15,
        smooth_shading=True,
        lighting=False,
    )

    # Laser arrows
    for arr in meshes["laser_arrows"]:
        plotter.add_mesh(arr, color="#00ff88", opacity=0.6)

    # Detection arrow
    plotter.add_mesh(meshes["det_arrow"], color="#d7ff44", opacity=0.7)

    # Rotation axis
    plotter.add_mesh(
        meshes["rot_axis"],
        color="#ff4444",
        opacity=0.5,
        line_width=2,
        style="wireframe",
    )
    plotter.add_mesh(
        meshes["rot_ring"], color="#ff4444", opacity=0.5, line_width=2
    )
    plotter.add_mesh(meshes["rot_arrow"], color="#ff4444", opacity=0.6)

    # Reconstructed cuboid
    plotter.add_mesh(
        meshes["cuboid"],
        scalars="values",
        cmap="magma",
        opacity=0.9,
        smooth_shading=True,
        show_scalar_bar=False,
        specular=0.4,
    )


def add_legend(plotter, font_size=10):
    """Add color-coded legend text."""
    plotter.add_text(
        "Glass Capillary",
        position=(0.02, 0.92),
        font_size=font_size,
        color="#4499cc",
        viewport=True,
    )
    plotter.add_text(
        "Light Sheet (XY plane)",
        position=(0.02, 0.87),
        font_size=font_size,
        color="#00ff88",
        viewport=True,
    )
    plotter.add_text(
        "Rotation Axis (X)",
        position=(0.02, 0.82),
        font_size=font_size,
        color="#ff4444",
        viewport=True,
    )
    plotter.add_text(
        "Detection (Z)",
        position=(0.02, 0.77),
        font_size=font_size,
        color="#ff8844",
        viewport=True,
    )
    plotter.add_text(
        "Reconstructed Cuboid",
        position=(0.02, 0.72),
        font_size=font_size,
        color="#ffaa44",
        viewport=True,
    )


# =============================================
# 5. Render outputs
# =============================================
def render_perspective(meshes, output_path):
    """Render a high-resolution perspective view."""
    print("Rendering perspective view...")
    p = pv.Plotter(off_screen=True, window_size=(1920, 1080))
    add_scene(p, meshes)
    add_legend(p, font_size=10)
    p.add_text(
        "Lightsheet Fluorescence 3D Volume imaging — Nematostella vectensis: Light Sheet Fluorescence",
        position="upper_edge",
        font_size=14,
        color="white",
    )
    p.camera_position = [(50, -35, 25), (0, 0, 0), (0, 0, 1)]
    p.camera.zoom(0.85)
    p.screenshot(str(output_path))
    p.close()
    print(f"  Saved: {output_path}")


def render_4views(meshes, output_path):
    """Render four orthogonal views in a grid."""
    print("Rendering 4-view panel...")
    views = [
        ((45, -30, 25), "Perspective"),
        ((0, -40, 0), "Side view"),
        ((0, 0, 50), "Top view"),
        ((50, 0, 0), "End view"),
    ]

    p = pv.Plotter(
        shape=(1, 4), off_screen=True, window_size=(2400, 700), border=False
    )
    for idx, (cam_pos, title) in enumerate(views):
        p.subplot(0, idx)
        add_scene(p, meshes)
        p.add_text(title, position="upper_edge", font_size=12, color="white")
        p.camera_position = [cam_pos, (0, 0, 0), (0, 0, 1)]
        p.camera.zoom(1.1)

    p.screenshot(str(output_path))
    p.close()
    print(f"  Saved: {output_path}")


def render_animation(meshes, output_path, n_frames=N_ANIM_FRAMES, fps=ANIM_FPS):
    """Render a rotating GIF animation."""
    print(f"Rendering animation ({n_frames} frames)...")
    frames = []

    for fi in range(n_frames):
        angle = fi * 360 / n_frames
        elev = 22 + 12 * np.sin(np.radians(angle * 0.7))

        dist = 75
        cam_x = dist * np.cos(np.radians(angle)) * np.cos(np.radians(elev))
        cam_y = dist * np.sin(np.radians(angle)) * np.cos(np.radians(elev))
        cam_z = dist * np.sin(np.radians(elev))

        p = pv.Plotter(off_screen=True, window_size=(800, 800))
        add_scene(p, meshes)
        add_legend(p, font_size=8)
        p.add_text(
            "Lightsheet Fluorescence 3D Volume imaging — Nematostella vectensis Reconstruction",
            position="upper_edge",
            font_size=12,
            color="white",
        )
        p.camera_position = [(cam_x, cam_y, cam_z), (0, 0, 0), (0, 0, 1)]

        img = p.screenshot(return_img=True)
        frames.append(img)
        p.close()

        if (fi + 1) % 20 == 0:
            print(f"  Frame {fi + 1}/{n_frames}")

    imageio.mimsave(str(output_path), frames, fps=fps, loop=0)
    print(f"  Saved: {output_path}")


# =============================================
# Main
# =============================================
def main():
    # Generate + reconstruct
    camera_data = generate_phantom()
    volume = reconstruct(camera_data)
    meshes = build_meshes(volume)

    # Render all outputs
    render_perspective(meshes, OUTPUT_DIR / "Lightsheet Fluorescence 3D Volume imaging — Nematostella vectensis_pyvista_perspective.png")
    render_4views(meshes, OUTPUT_DIR / "Lightsheet Fluorescence 3D Volume imaging — Nematostella vectensis_pyvista_4views.png")
    render_animation(meshes, OUTPUT_DIR / "Lightsheet Fluorescence 3D Volume imaging — Nematostella vectensis_pyvista_anim.gif")

    print("\nAll done!")


if __name__ == "__main__":
    main()
