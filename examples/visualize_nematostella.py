"""
LSFT Nematostella Visualization with PyVista (High Detail)
==========================================================

Generates a detailed Nematostella-like phantom inside a glass capillary,
reconstructs a 3D volume via polar→Cartesian interpolation, and
renders smooth isosurface visualizations with PyVista.

Anatomical features:
  - 3-layer body wall (gastrodermis, mesoglea, epidermis)
  - 8 mesenteries with retractor muscles and filaments
  - Elliptical pharynx (siphonoglyph-like)
  - Oral disc with mouth opening and radial grooves
  - 16 tentacles (varying length, thickness, curl)
  - Pedal disc at aboral end

Outputs:
  - lsft_nema_hd_perspective.png   (high-res single view)
  - lsft_nema_hd_4views.png        (4 orthogonal views)
  - lsft_nema_hd_oral.png          (oral end close-up)
  - lsft_nema_hd_anim.gif          (rotating animation)

Requirements:
  pip install pyvista vtk numpy scipy scikit-image imageio

Usage:
  python visualize_nematostella.py
"""

import sys
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pyvista as pv
from scipy.ndimage import gaussian_filter

# Add parent src to path for plugin imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from napari_lsft._reconstruction import reconstruct_volume

# =============================================
# Configuration
# =============================================
OUTPUT_DIR = Path(__file__).resolve().parent

# Phantom resolution
NX_PHANTOM = 250
NY_PHANTOM = 128
NZ_PHANTOM = 128

# Acquisition parameters
N_ANGLES = 180
N_X_CAM = 250
N_Y_CAM = 56
CS_OUTPUT = 128       # reconstruction output size (YZ)

# Capillary geometry
CAP_INNER = 12.0
CAP_OUTER = 13.5

# Animation
N_ANIM_FRAMES = 150
ANIM_FPS = 20
SAMPLE_ROT_SPEED = 0.8   # degrees per frame (slow sample rotation)
CAM_ORBIT_SPEED = 0.2    # very slow camera orbit
CAM_DISTANCE = 110       # far out

# Static view cameras
PERSPECTIVE_CAM_POS = (80, -65, 45)
PERSPECTIVE_ZOOM = 0.7
FOUR_VIEW_ZOOM = 0.7

# PyVista setup
pv.OFF_SCREEN = True
pv.global_theme.background = [8, 10, 18]
pv.global_theme.font.color = "white"


# =============================================
# 1. Build high-detail Nematostella phantom
# =============================================
def build_nematostella_phantom():
    """
    Create a detailed 3D volume mimicking Nematostella vectensis.

    Anatomical layers:
      - Epidermis (outer, bright)
      - Mesoglea (middle, dim jelly layer)
      - Gastrodermis (inner tissue)
      - Gastrovascular cavity (dark center)
      - 8 mesenteries with retractor muscles + filaments
      - Elliptical pharynx with wall
      - 16 tentacles with core/sheath structure
      - Oral disc with mouth + radial grooves
      - Pedal disc (foot)
    """
    nx, ny, nz = NX_PHANTOM, NY_PHANTOM, NZ_PHANTOM
    x_v = np.linspace(-35, 35, nx)
    y_v = np.linspace(-18, 18, ny)
    z_v = np.linspace(-18, 18, nz)
    X3, Y3, Z3 = np.meshgrid(x_v, y_v, z_v, indexing="ij")
    R_yz = np.sqrt(Y3**2 + Z3**2)

    nema = np.zeros((nx, ny, nz), dtype=np.float32)

    # Per-structure sub-volumes for multi-colour visualization
    s_epi  = np.zeros((nx, ny, nz), dtype=np.float32)   # epidermis
    s_gast = np.zeros((nx, ny, nz), dtype=np.float32)   # gastrodermis
    s_mes  = np.zeros((nx, ny, nz), dtype=np.float32)   # mesenteries
    s_pha  = np.zeros((nx, ny, nz), dtype=np.float32)   # pharynx
    s_oral = np.zeros((nx, ny, nz), dtype=np.float32)   # oral disc
    s_tent = np.zeros((nx, ny, nz), dtype=np.float32)   # tentacles
    s_ped  = np.zeros((nx, ny, nz), dtype=np.float32)   # pedal disc

    # --- Body radius profile along X ---
    # Oral end (tentacles) at NEGATIVE X, foot at POSITIVE X
    body_radius = np.zeros(nx)
    for i, x in enumerate(x_v):
        if x < -25:
            body_radius[i] = 0
        elif x < -21:
            # No body before the oral face — creates a flat front
            body_radius[i] = 0
        elif x < -12:
            # Oral disc: flat face, same diameter as body column
            body_radius[i] = 4.5
        elif x < 16:
            # Main body column: fairly uniform, very gentle taper
            t = (x + 12) / 28
            body_radius[i] = 4.5 - 0.4 * t + 0.12 * np.sin(t * 2.5 * np.pi)
        elif x < 21:
            # Constriction / neck before pedal disc (gentler)
            t = (x - 16) / 5
            body_radius[i] = 4.3 - 1.4 * t
        elif x < 33:
            # Pedal disc: same diameter as body column, disc-shaped
            t = (x - 21) / 12
            if t < 0.25:
                # Rapid flare from constriction back up to body width
                body_radius[i] = 2.9 + 1.6 * (t / 0.25) ** 0.5
            elif t < 0.75:
                # Flat disc face, same width as body column
                body_radius[i] = 4.5
            else:
                # Back face rounds off to close
                body_radius[i] = 4.5 * (1 - ((t - 0.75) / 0.25) ** 1.5)
        else:
            body_radius[i] = 0

    # --- Layered body wall ---
    print("  Body layers...")
    for i in range(nx):
        r = body_radius[i]
        if r <= 0:
            continue
        R_s = R_yz[i]

        # Gastrodermis (inner tissue)
        gast_mask = R_s < r * 0.85
        nema[i][gast_mask] = 0.30
        s_gast[i][gast_mask] = 0.80

        # Mesoglea (jelly layer, dimmer)
        mesoglea = (R_s >= r * 0.85) & (R_s < r * 0.92)
        nema[i][mesoglea] = 0.20

        # Epidermis (outer layer, brightest)
        epidermis = (R_s >= r * 0.92) & (R_s < r)
        nema[i][epidermis] = 0.55
        s_epi[i][epidermis] = 1.0

        # Gastrovascular cavity
        x = x_v[i]
        if r > 2.5:
            cavity_r = r * (0.35 if x < -16 else 0.28)
            nema[i][R_s < cavity_r] = 0.08
            s_gast[i][R_s < cavity_r] = 0.0   # hollow out cavity

    # --- 8 mesenteries with retractor muscles + filaments ---
    print("  Mesenteries...")
    for m in range(8):
        angle = m * 2 * np.pi / 8
        my, mz = np.cos(angle), np.sin(angle)
        for i in range(nx):
            r = body_radius[i]
            if r < 3.5 or x_v[i] < -18:
                continue

            dist_to_line = np.abs(Y3[i] * mz - Z3[i] * my)
            radial_pos = Y3[i] * my + Z3[i] * mz

            # Thin radial sheet
            sheet = (
                (dist_to_line < 0.35)
                & (radial_pos > r * 0.22)
                & (radial_pos < r * 0.82)
            )
            nema[i][sheet] = np.maximum(nema[i][sheet], 0.65)
            s_mes[i][sheet] = np.maximum(s_mes[i][sheet], 0.90)

            # Retractor muscle (thickened node)
            ret_r = r * 0.55
            ret_d = np.sqrt(
                (Y3[i] - ret_r * my) ** 2 + (Z3[i] - ret_r * mz) ** 2
            )
            nema[i][ret_d < 0.45] = np.maximum(nema[i][ret_d < 0.45], 0.75)
            s_mes[i][ret_d < 0.45] = np.maximum(s_mes[i][ret_d < 0.45], 1.0)

            # Mesentery filament (at inner edge)
            fil_r = r * 0.25
            fil_d = np.sqrt(
                (Y3[i] - fil_r * my) ** 2 + (Z3[i] - fil_r * mz) ** 2
            )
            nema[i][fil_d < 0.25] = np.maximum(nema[i][fil_d < 0.25], 0.70)
            s_mes[i][fil_d < 0.25] = np.maximum(s_mes[i][fil_d < 0.25], 0.95)

    # --- Pharynx (elliptical, siphonoglyph-like) ---
    print("  Pharynx...")
    for i in range(nx):
        x, r = x_v[i], body_radius[i]
        if -21 < x < -9 and r > 3:    # 20% longer extent
            pry = min(0.44, r * 0.048)  # 20% smaller cross-section
            prz = min(0.28, r * 0.032)
            pm = (Y3[i] ** 2 / pry**2 + Z3[i] ** 2 / prz**2) < 1
            nema[i][pm] = 0.82
            s_pha[i][pm] = 1.0
            pw = (
                (Y3[i] ** 2 / (pry * 1.3) ** 2 + Z3[i] ** 2 / (prz * 1.3) ** 2)
                < 1
            ) & ~pm
            nema[i][pw] = 0.70
            s_pha[i][pw] = 0.65

    # --- Oral disc with mouth + grooves ---
    print("  Oral disc...")
    mouth_r = 1.8  # cylindrical mouth tube radius
    for i in range(nx):
        x = x_v[i]
        r = body_radius[i]

        # Dark mouth tube: runs inward through the oral disc
        if -21 < x < -12 and r > mouth_r:
            nema[i][R_yz[i] < mouth_r] = 0.03  # near-black interior

        # Radial grooves across the oral disc face
        if -21 < x < -12 and r > 4:
            for m in range(16):
                a = m * 2 * np.pi / 16
                gd = np.abs(Y3[i] * np.sin(a) - Z3[i] * np.cos(a))
                gr = Y3[i] * np.cos(a) + Z3[i] * np.sin(a)
                groove = (gd < 0.15) & (gr > 2) & (gr < r * 0.9)
                nema[i][groove] = np.maximum(nema[i][groove], 0.48)
                s_oral[i][groove] = np.maximum(s_oral[i][groove], 0.90)

    # --- 16 tentacles (longer, slimmer, less curled, more outward) ---
    print("  Tentacles...")
    np.random.seed(42)
    for t_idx in range(16):
        angle = t_idx * 2 * np.pi / 16

        base_r = 4.4
        base_x = -21.0
        base_y = base_r * np.cos(angle)
        base_z = base_r * np.sin(angle)

        tent_len = 7.8 + np.random.rand() * 7.8
        tent_thick = 0.5 + np.random.rand() * 0.25
        curl_freq = 0.6 + 0.4 * np.random.rand()
        curl_amp_max = 1.0 + 1.2 * np.random.rand()
        droop = 0.2 * np.random.rand()

        for p in range(60):
            frac = p / 60

            tx = base_x - tent_len * frac * (
                0.9 + 0.1 * np.sin(frac * np.pi)
            )
            outward = 1.0 + frac * 0.5
            ca = curl_amp_max * frac * (1 - 0.3 * frac)
            cy_ = ca * np.sin(
                curl_freq * frac * 2 * np.pi + t_idx * 1.1
            )
            cz_ = ca * np.cos(
                curl_freq * frac * 2 * np.pi + t_idx * 0.7
            )
            ty = (
                base_y * outward
                + cy_ * np.cos(angle)
                - cz_ * np.sin(angle)
            )
            tz = (
                base_z * outward
                + cy_ * np.sin(angle)
                + cz_ * np.cos(angle)
            )
            tz -= droop * frac * tent_len

            rt = tent_thick * (1 - frac * 0.65)
            intensity = 0.50 + 0.25 * (1 - frac)

            d = np.sqrt(
                (X3 - tx) ** 2 + (Y3 - ty) ** 2 + (Z3 - tz) ** 2
            )
            # Brighter core
            nema[d < rt * 0.5] = np.maximum(
                nema[d < rt * 0.5], intensity + 0.1
            )
            s_tent[d < rt * 0.5] = np.maximum(s_tent[d < rt * 0.5], 0.90)
            # Outer sheath
            outer = (d >= rt * 0.5) & (d < rt)
            nema[outer] = np.maximum(nema[outer], intensity)
            s_tent[outer] = np.maximum(s_tent[outer], 0.72)

    # --- Pedal disc (foot) ---
    for i in range(nx):
        x = x_v[i]
        if 21 < x < 33 and body_radius[i] > 0.5:
            r = body_radius[i]
            foot = R_yz[i] < r

            # Disc tissue
            nema[i][foot] = np.maximum(nema[i][foot], 0.45)
            s_ped[i][foot] = 0.80

            # Bright epithelial rim (outer surface ring)
            disc_rim = (R_yz[i] > r * 0.88) & foot
            nema[i][disc_rim] = np.maximum(nema[i][disc_rim], 0.68)
            s_ped[i][disc_rim] = 1.0

            # Radial wrinkles / ridges (8 ridges = 16 visible from end)
            for ridx in range(8):
                ra = ridx * np.pi / 8
                ridge_d = np.abs(Y3[i] * np.sin(ra) - Z3[i] * np.cos(ra))
                ridge_r = Y3[i] * np.cos(ra) + Z3[i] * np.sin(ra)
                ridge = (ridge_d < 0.2) & (ridge_r > 0.5) & (ridge_r < r * 0.88) & foot
                nema[i][ridge] = np.maximum(nema[i][ridge], 0.60)
                s_ped[i][ridge] = np.maximum(s_ped[i][ridge], 0.95)

    # --- Surface texture + smoothing ---
    print("  Texture + smoothing...")
    noise = np.random.rand(nx, ny, nz).astype(np.float32) * 0.04
    nema[nema > 0.1] += noise[nema > 0.1]
    nema = np.clip(nema, 0, 1)
    nema = gaussian_filter(nema, sigma=0.35)

    # Smooth per-structure volumes and package
    structures = {
        "epidermis":    gaussian_filter(s_epi,  sigma=0.5),
        "gastrodermis": gaussian_filter(s_gast, sigma=0.5),
        "mesenteries":  gaussian_filter(s_mes,  sigma=0.4),
        "pharynx":      gaussian_filter(s_pha,  sigma=0.4),
        "oral_disc":    gaussian_filter(s_oral, sigma=0.3),
        "tentacles":    gaussian_filter(s_tent, sigma=0.3),
        "pedal_disc":   gaussian_filter(s_ped,  sigma=0.5),
    }

    print(f"  Done: {nema.shape}, non-zero={np.sum(nema > 0.01)}")
    return nema, structures


# =============================================
# 2. Simulate camera acquisition
# =============================================
def simulate_acquisition(nema):
    """Simulate camera data from phantom with capillary wall."""
    nx, ny, nz = nema.shape
    angles_rad = np.linspace(0, np.pi, N_ANGLES, endpoint=False)
    y_lab = np.linspace(-CAP_OUTER, CAP_OUTER, N_Y_CAM)

    camera_data = np.zeros((N_ANGLES, N_X_CAM, N_Y_CAM), dtype=np.float32)

    print("  Simulating camera frames...")
    for ia, theta in enumerate(angles_rad):
        ct, st = np.cos(theta), np.sin(theta)
        for jy, yl in enumerate(y_lab):
            ys, zs = yl * ct, yl * st
            rv = np.sqrt(ys**2 + zs**2)
            cv = 0.12 if CAP_INNER <= rv <= CAP_OUTER else 0.0
            iy = int((ys + 18) / 36 * (ny - 1))
            iz = int((zs + 18) / 36 * (nz - 1))
            if 0 <= iy < ny and 0 <= iz < nz:
                camera_data[ia, :, jy] = np.maximum(nema[:, iy, iz], cv)
            else:
                camera_data[ia, :, jy] = cv
        if (ia + 1) % 60 == 0:
            print(f"    Angle {ia + 1}/{N_ANGLES}")

    print(f"  Camera data: {camera_data.shape}")
    return camera_data


# =============================================
# 3. Reconstruct
# =============================================
def reconstruct(camera_data):
    """Reconstruct and smooth volume."""
    print("  Reconstructing...")
    volume = reconstruct_volume(
        camera_data,
        angle_start=0,
        angle_stop=180,
        auto_center=False,
        center_offset=0.0,
        output_size=CS_OUTPUT,
        n_workers=4,
    )
    volume_smooth = gaussian_filter(volume, sigma=0.5)
    print(f"  Volume: {volume_smooth.shape}")
    return volume_smooth


# =============================================
# Per-structure mesh parameters (naturalistic, muted colours)
# (name, hex_color, opacity, smooth_iters, isosurface_threshold)
# =============================================
# Rendered inside-out for correct alpha blending (deepest structure first)
STRUCTURE_VIZ = [
    ("gastrodermis", "#e0c040", 0.30, 25, 0.35),  # golden, inner tissue
    ("pharynx",      "#c06050", 0.42, 12, 0.48),  # muted rose, thin central tube
    ("oral_disc",    "#d09050", 0.30, 15, 0.38),  # warm peach, oral grooves
    ("mesenteries",  "#88a870", 0.14, 15, 0.52),  # sage — LOW opacity, thin sheets only
    ("epidermis",    "#f0ead0", 0.22, 35, 0.35),  # warm cream, outer shell
    ("pedal_disc",   "#a878b8", 0.55, 20, 0.38),  # soft purple, foot balloon
    ("tentacles",    "#a0c8d8", 0.82, 18, 0.38),  # steel blue, prominent arms
]

# =============================================
# 4. Build PyVista meshes (5-level isosurfaces)
# =============================================

# Isosurface parameters: (name, threshold, smooth_iterations)
ISO_PARAMS = [
    ("outer", 0.10, 50),
    ("body", 0.22, 35),
    ("wall", 0.38, 20),
    ("bright", 0.52, 12),
    ("core", 0.68, 8),
]

# Isosurface rendering layers: (key, color, opacity)
ISO_LAYERS = [
    ("outer", "#f5ead0", 0.05),
    ("body", "#e8d4a0", 0.12),
    ("wall", "#d4a870", 0.30),
    ("bright", "#cc8844", 0.55),
    ("core", "#ee7733", 0.80),
]


def build_meshes(volume, structures=None):
    """Build per-structure phantom meshes, reconstruction isosurfaces, and setup geometry."""
    x_extent = 35 * (N_X_CAM / CS_OUTPUT)
    x_half = x_extent / 2
    voxel_x = x_extent / volume.shape[0]
    voxel_yz = 36.0 / CS_OUTPUT

    # Volume grid
    grid = pv.ImageData()
    grid.dimensions = np.array(volume.shape) + 1
    grid.origin = (-x_half, -18, -18)
    grid.spacing = (voxel_x, voxel_yz, voxel_yz)
    grid.cell_data["values"] = volume.flatten(order="F")
    grid_pts = grid.cell_data_to_point_data()

    # Extract and smooth isosurfaces
    isos = {}
    for name, val, niter in ISO_PARAMS:
        iso = grid_pts.contour([val], scalars="values")
        if iso.n_points > 0:
            iso = iso.smooth(n_iter=niter, relaxation_factor=0.1)
        isos[name] = iso
        print(f"  {name}: {iso.n_points} pts")

    # Capillary
    cap_mesh = pv.Cylinder(
        center=(0, 0, 0),
        direction=(1, 0, 0),
        radius=CAP_INNER,
        height=x_extent * 0.96,
        resolution=120,
        capping=True,
    )

    # Light sheet
    light_sheet = pv.Plane(
        center=(0, 0, 0),
        direction=(0, 0, 1),
        i_size=x_extent * 1.05,
        j_size=CAP_INNER * 2.8,
    )

    # Laser arrows
    laser_arrows = [
        pv.Arrow(
            start=(xa, -CAP_INNER * 1.8, 0),
            direction=(0, 1, 0),
            scale=CAP_INNER * 0.5,
            tip_length=0.3,
            tip_radius=0.1,
            shaft_radius=0.03,
        )
        for xa in np.linspace(-x_half * 0.5, x_half * 0.5, 7)
    ]

    # Detection arrow + camera icon
    det_arrow = pv.Arrow(
        start=(0, 0, -22),
        direction=(0, 0, 1),
        scale=5,
        tip_length=0.25,
        tip_radius=0.12,
        shaft_radius=0.05,
    )
    cam_box = pv.Box(bounds=(-2.5, 2.5, -2, 2, -26, -22))

    # Rotation axis
    rot_axis = pv.Line((-x_half * 1.15, 0, 0), (x_half * 1.15, 0, 0))

    # Per-structure isosurface meshes from phantom volumes
    struct_meshes = {}
    if structures is not None:
        ph_vox_x = 70.0 / NX_PHANTOM
        ph_vox_yz = 36.0 / NY_PHANTOM
        for name, _color, _opacity, niter, thresh in STRUCTURE_VIZ:
            svol = structures.get(name)
            if svol is None:
                continue
            sg = pv.ImageData()
            sg.dimensions = np.array(svol.shape) + 1
            sg.origin = (-35.0, -18.0, -18.0)
            sg.spacing = (ph_vox_x, ph_vox_yz, ph_vox_yz)
            sg.cell_data["values"] = svol.flatten(order="F")
            sg_pts = sg.cell_data_to_point_data()
            if svol.max() > thresh * 0.5:
                siso = sg_pts.contour([thresh], scalars="values")
                if siso.n_points > 0:
                    siso = siso.smooth(n_iter=niter, relaxation_factor=0.1)
                struct_meshes[name] = siso
                print(f"  struct/{name}: {siso.n_points} pts")
            else:
                struct_meshes[name] = pv.PolyData()

    return {
        "isos": isos,
        "struct_meshes": struct_meshes,
        "cap_mesh": cap_mesh,
        "light_sheet": light_sheet,
        "laser_arrows": laser_arrows,
        "det_arrow": det_arrow,
        "cam_box": cam_box,
        "rot_axis": rot_axis,
        "x_half": x_half,
    }


# =============================================
# 5. Scene assembly helpers
# =============================================
def add_fixed_scene(plotter, meshes):
    """Add lab-frame elements."""
    plotter.add_mesh(
        meshes["light_sheet"], color="#00ff88", opacity=0.08, lighting=False
    )
    for arr in meshes["laser_arrows"]:
        plotter.add_mesh(arr, color="#00ff88", opacity=0.4)
    plotter.add_mesh(meshes["det_arrow"], color="#ff8844", opacity=0.55)
    plotter.add_mesh(meshes["cam_box"], color="#ff8844", opacity=0.2)
    plotter.add_mesh(
        meshes["rot_axis"],
        color="#ff4444",
        opacity=0.25,
        line_width=2,
        style="wireframe",
    )


def add_sample(plotter, meshes, rot_angle=0):
    """Add rotating sample (capillary + Nematostella isosurfaces)."""

    def maybe_rotate(mesh):
        m = mesh.copy()
        if rot_angle != 0:
            m = m.rotate_x(rot_angle, point=(0, 0, 0))
        return m

    # Glass capillary
    plotter.add_mesh(
        maybe_rotate(meshes["cap_mesh"]),
        color="#aad4ee",
        opacity=0.10,
        smooth_shading=True,
        specular=1.0,
        specular_power=80,
        diffuse=0.35,
        ambient=0.30,
    )

    # Nematostella: per-structure colours when available, else 5-level fallback
    if meshes.get("struct_meshes"):
        for name, color, opacity, _, _ in STRUCTURE_VIZ:
            mesh = meshes["struct_meshes"].get(name)
            if mesh is not None and mesh.n_points > 0:
                plotter.add_mesh(
                    maybe_rotate(mesh),
                    color=color,
                    opacity=opacity,
                    smooth_shading=True,
                    specular=0.30,
                    specular_power=25,
                )
    else:
        for key, color, opacity in ISO_LAYERS:
            iso = meshes["isos"][key]
            if iso.n_points > 0:
                plotter.add_mesh(
                    maybe_rotate(iso),
                    color=color,
                    opacity=opacity,
                    smooth_shading=True,
                    specular=0.25,
                    specular_power=20,
                )


def add_labels(plotter, rot_angle=None, font_size=9):
    """Add color-coded legend."""
    labels = [
        (0.94, "Glass capillary", "#88bbdd"),
        (0.89, "Light sheet",     "#00ff88"),
        (0.84, "Detection",       "#ff8844"),
        (0.79, "Rot. axis",       "#ff4444"),
        (0.74, "Epidermis",       "#f0ead0"),
        (0.69, "Gastrodermis",    "#e0c040"),
        (0.64, "Mesenteries",     "#88a870"),
        (0.59, "Pharynx",         "#c06050"),
        (0.54, "Oral disc",       "#d09050"),
        (0.49, "Tentacles",       "#a0c8d8"),
        (0.44, "Pedal disc",      "#a878b8"),
    ]
    for y, text, color in labels:
        plotter.add_text(
            text,
            position=(0.02, y),
            font_size=font_size,
            color=color,
            viewport=True,
        )
    if rot_angle is not None:
        plotter.add_text(
            f"θ = {rot_angle:.0f}°",
            position=(0.02, 0.59),
            font_size=font_size,
            color="#ff6666",
            viewport=True,
        )


# =============================================
# 6. Render outputs
# =============================================
def render_perspective(meshes, output_path):
    """High-resolution perspective view."""
    print("  Rendering perspective...")
    p = pv.Plotter(off_screen=True, window_size=(2560, 1440))
    add_fixed_scene(p, meshes)
    add_sample(p, meshes)
    p.add_text(
        "LSFT — Nematostella vectensis",
        position="upper_edge",
        font_size=16,
        color="white",
    )
    add_labels(p, font_size=11)
    p.camera_position = [PERSPECTIVE_CAM_POS, (0, 0, 0), (0, 0, 1)]
    p.camera.zoom(PERSPECTIVE_ZOOM)
    p.screenshot(str(output_path))
    p.close()
    print(f"    Saved: {output_path}")


def render_4views(meshes, output_path):
    """Four orthogonal views."""
    print("  Rendering 4-view panel...")
    views = [
        ((70, -60, 40), "Perspective"),
        ((0, -80, 0), "Side"),
        ((0, 0, 80), "Top"),
        ((90, 0, 0), "End (oral)"),
    ]
    p = pv.Plotter(
        shape=(1, 4), off_screen=True, window_size=(3200, 900), border=False
    )
    for idx, (cpos, title) in enumerate(views):
        p.subplot(0, idx)
        add_fixed_scene(p, meshes)
        add_sample(p, meshes)
        p.add_text(title, position="upper_edge", font_size=12, color="white")
        p.camera_position = [cpos, (0, 0, 0), (0, 0, 1)]
        p.camera.zoom(FOUR_VIEW_ZOOM)
    p.screenshot(str(output_path))
    p.close()
    print(f"    Saved: {output_path}")


def render_oral_closeup(meshes, output_path):
    """Close-up of oral end showing tentacles and pharynx."""
    print("  Rendering oral close-up...")
    p = pv.Plotter(off_screen=True, window_size=(1920, 1080))
    add_sample(p, meshes)
    p.add_text(
        "Oral End — Tentacles & Pharynx",
        position="upper_edge",
        font_size=14,
        color="white",
    )
    p.camera_position = [(-75, -15, 10), (-25, 0, 0), (0, 0, 1)]
    p.camera.zoom(1.3)
    p.screenshot(str(output_path))
    p.close()
    print(f"    Saved: {output_path}")


def render_animation(meshes, output_path):
    """
    Rotating GIF animation.

    Capillary + Nematostella rotate slowly around X axis.
    Camera orbits very slowly for gentle viewing angle change.
    """
    print(f"  Rendering animation ({N_ANIM_FRAMES} frames, "
          f"rot={SAMPLE_ROT_SPEED}°/frame, dist={CAM_DISTANCE})...")
    frames = []

    for fi in range(N_ANIM_FRAMES):
        cam_angle = fi * 360 / N_ANIM_FRAMES * CAM_ORBIT_SPEED + 200
        cam_elev = 16 + 8 * np.sin(
            np.radians(fi * 360 / N_ANIM_FRAMES * 0.3)
        )
        sample_rot = fi * SAMPLE_ROT_SPEED

        p = pv.Plotter(off_screen=True, window_size=(1100, 1000))
        add_fixed_scene(p, meshes)
        add_sample(p, meshes, rot_angle=sample_rot)

        p.add_text(
            "LSFT — Nematostella vectensis",
            position="upper_edge",
            font_size=12,
            color="white",
        )
        add_labels(p, rot_angle=sample_rot, font_size=8)

        cx = CAM_DISTANCE * np.cos(np.radians(cam_angle)) * np.cos(
            np.radians(cam_elev)
        )
        cy = CAM_DISTANCE * np.sin(np.radians(cam_angle)) * np.cos(
            np.radians(cam_elev)
        )
        cz = CAM_DISTANCE * np.sin(np.radians(cam_elev))
        p.camera_position = [(cx, cy, cz), (0, 0, 0), (0, 0, 1)]

        frames.append(p.screenshot(return_img=True))
        p.close()

        if (fi + 1) % 25 == 0:
            print(f"    Frame {fi + 1}/{N_ANIM_FRAMES}")

    imageio.mimsave(str(output_path), frames, fps=ANIM_FPS, loop=0)
    print(f"    Saved: {output_path}")


# =============================================
# Main
# =============================================
def main():
    print("=" * 60)
    print("LSFT Nematostella Visualization (High Detail)")
    print("=" * 60)

    print("\n1. Building Nematostella phantom...")
    nema, structures = build_nematostella_phantom()

    print("\n2. Simulating camera acquisition...")
    camera_data = simulate_acquisition(nema)

    print("\n3. Reconstructing volume...")
    volume = reconstruct(camera_data)

    print("\n4. Building PyVista meshes (structure colours + isosurfaces)...")
    meshes = build_meshes(volume, structures)

    print("\n5. Rendering outputs...")
    render_perspective(
        meshes, OUTPUT_DIR / "lsft_nema_hd_perspective.png"
    )
    render_4views(meshes, OUTPUT_DIR / "lsft_nema_hd_4views.png")
    render_oral_closeup(meshes, OUTPUT_DIR / "lsft_nema_hd_oral.png")
    render_animation(meshes, OUTPUT_DIR / "lsft_nema_hd_anim.gif")

    print("\n" + "=" * 60)
    print("All done!")
    print("=" * 60)


if __name__ == "__main__":
    main()
