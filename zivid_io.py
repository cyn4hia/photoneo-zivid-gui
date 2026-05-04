from __future__ import annotations
from pathlib import Path
from typing import Literal, Tuple, Dict, Any
import numpy as np
import open3d as o3d

try:
    from PIL import Image
except Exception:
    Image = None  # The app surfaces a clear error if PIL is missing.


def _save_png_u16(path: Path, arr_u16: np.ndarray):
    if Image is None:
        raise RuntimeError("Pillow (PIL) is required to save PNG (I;16).")
    if arr_u16.ndim != 2 or arr_u16.dtype != np.uint16:
        raise ValueError(f"Expected 2D uint16 array, got shape={arr_u16.shape} dtype={arr_u16.dtype}")
    Image.fromarray(arr_u16, mode="I;16").save(str(path))


def _save_png_rgba(path: Path, rgba: np.ndarray):
    if Image is None:
        raise RuntimeError("Pillow (PIL) is required to save PNG (RGBA).")
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
        raise ValueError(f"Expected HxWx4 uint8 RGBA, got shape={rgba.shape} dtype={rgba.dtype}")
    Image.fromarray(rgba, mode="RGBA").save(str(path))


def save_zivid_outputs_scaled(
    frame,
    prefix: Path,
    save_zdf: bool = True,
    save_rgba_png: bool = True,
    depth_mode: Literal["off", "u16_scaled", "u16_mm", "float_npy"] = "u16_scaled",
    user_min_m: float | None = 0.3,
    user_max_m: float | None = 1.1,
    save_snr_png: bool = False,
    save_normal_png: bool = False,
    save_ply: bool = True,
) -> Tuple[Dict[str, Path], Dict[str, Any]]:
    """
    Current version assumes using zivid 2 plus mr60 model.
    Returns:
      written: {artifact_key -> Path}
      depth_meta: dict (or {} if no depth)
    Artifact keys: "raw", "rgb", "depth", "snr", "normal", "pc"
    """
    written: Dict[str, Path] = {}
    depth_meta: Dict[str, Any] = {}
    prefix = Path(prefix)

    # --- RAW ZDF ---
    if save_zdf:
        p = prefix.with_name(prefix.name + "_raw.zdf")
        frame.save(str(p))
        written["raw"] = p

    # Prefer point-cloud RGBA (works across SDKs); fallback to 2D image_rgba when available
    def _get_rgba_or_none():
        # 1) point cloud plane
        try:
            pc = frame.point_cloud()
            rgba = pc.copy_data("rgba")
            if rgba is not None and rgba.ndim == 3 and rgba.shape[2] >= 4:
                return rgba[:, :, :4].astype(np.uint8)
        except Exception:
            pass
        # 2) 2D API on some SDKs
        try:
            f2d = frame.frame_2d()
            img_rgba = f2d.image_rgba()
            # many SDKs expose .copy_data() or .to_numpy()
            for attr in ("copy_data", "to_numpy", "data"):
                if hasattr(img_rgba, attr):
                    arr = getattr(img_rgba, attr)()
                    arr = np.array(arr)
                    if arr.ndim == 3 and arr.shape[2] == 4 and arr.dtype == np.uint8:
                        return arr
        except Exception:
            pass
        return None

    # --- RGBA PNG ---
    if save_rgba_png:
        rgba = _get_rgba_or_none()
        if rgba is None:
            raise RuntimeError("Zivid: RGBA image not available from this SDK/frame.")
        p = prefix.with_name(prefix.name + "_rgb.png")
        _save_png_rgba(p, rgba)
        written["rgb"] = p

    # --- Depth / Normals / SNR / PLY via point cloud ---
    need_pc = (depth_mode != "off") or save_snr_png or save_normal_png or save_ply
    pc = None
    if need_pc:
        try:
            pc = frame.point_cloud()
            _ = pc.copy_data("xyz")
        except Exception as e:
            raise RuntimeError(f"Zivid: point cloud not available: {e}")

    # Depth
    if depth_mode != "off":
        if pc is None:
            raise RuntimeError("PLY requested but point cloud not available")
        z_m = pc.copy_data("z")
        if z_m is None or z_m.ndim != 2:
            raise RuntimeError(f"Unexpected depth shape: {None if z_m is None else z_m.shape}")
        z_m = np.nan_to_num(z_m, nan=0.0, posinf=0.0, neginf=0.0)
        valid = z_m > 0
        if valid.any():
            scene_med = float(np.percentile(z_m[valid], 50))
            if scene_med > 10:
                # likely the unit is in meters, not millimeters
                z_m = z_m / 1000.0
            scene_min = float(np.percentile(z_m[valid], 0.1))
            scene_max = float(np.percentile(z_m[valid], 99.9))
        else:
            scene_min, scene_max = 0.0, 1.0

        if depth_mode == "float_npy":
            p = prefix.with_name(prefix.name + "_depth.npy")
            np.save(str(p), z_m.astype(np.float32))
            written["depth"] = p
            depth_meta = {"encoding": "float32_m", "invalid_zero": True}
        elif depth_mode == "u16_mm":
            p = prefix.with_name(prefix.name + "_depth.png")
            z_u16 = np.where(valid, np.clip(z_m * 1000.0, 0, 65535), 0).astype(np.uint16)
            _save_png_u16(p, z_u16)
            written["depth"] = p
            depth_meta = {"min_m": 0.0, "max_m": 65.535, "encoding": "u16_mm", "invalid_zero": True}
        else:  # u16_scaled
            min_used = max(user_min_m if user_min_m is not None else scene_min, scene_min)
            max_used = min(user_max_m if user_max_m is not None else scene_max, scene_max)
            if max_used <= min_used:
                max_used = min_used + 1e-6
            scale = 65534.0 / (max_used - min_used)
            z_u16 = np.where(valid, np.clip((z_m - min_used) * scale + 1.0, 1.0, 65535.0), 0.0).astype(np.uint16)
            p = prefix.with_name(prefix.name + "_depth.png")
            _save_png_u16(p, z_u16)
            written["depth"] = p
            depth_meta = {"min_m": float(min_used), "max_m": float(max_used), "encoding": "u16_scaled", "invalid_zero": True}

    # SNR
    if save_snr_png:
        if pc is None:
            raise RuntimeError("PLY requested but point cloud not available")
        snr = pc.copy_data("snr")
        if snr is None:
            raise RuntimeError("SNR plane not available")
        snr = np.nan_to_num(snr, nan=0.0)
        snr_u8 = np.clip(snr, 0, 255).astype(np.uint8)
        if Image is None:
            raise RuntimeError("Pillow not available for SNR PNG")
        p = prefix.with_name(prefix.name + "_snr.png")
        Image.fromarray(snr_u8, mode="L").save(str(p))
        written["snr"] = p

    # Normals
    if save_normal_png:
        if pc is None:
            raise RuntimeError("PLY requested but point cloud not available")
        n = pc.copy_data("normals")
        if n is None or n.ndim != 3 or n.shape[2] < 3:
            raise RuntimeError(f"Normals not available or wrong shape: {None if n is None else n.shape}")
        n = np.nan_to_num(n, nan=0.0)
        valid = np.linalg.norm(n[..., :3], axis=2) > 1e-6
        # standard way to visualize normals: 
        # nx,ny to rg from [-1,1] to [0,255]
        # nz from [0, -1] to [128, 255]
        n8 = np.zeros_like(n, dtype=np.float32)
        n8[...,0:2] = (n[...,0:2] * 0.5 + 0.5) * 255.0
        n8[...,2] = (-n[...,2] * 0.5 + 0.5) * 255.0
        n8 = n8.clip(0, 255).astype(np.uint8)
        n8[~valid,:] = 0

        if Image is None:
            raise RuntimeError("Pillow not available for normals PNG")
        p = prefix.with_name(prefix.name + "_normal.png")
        Image.fromarray(n8[:, :, :3], mode="RGB").save(str(p))
        written["normal"] = p

    # PLY
    # PLY
    # PLY
    if save_ply:
        if pc is None:
            raise RuntimeError("PLY requested but point cloud not available")
        p = prefix.with_name(prefix.name + "_pc.ply")
        saved = False
        ply_errors: list[str] = []

        # Try open3d first (preferred - smaller, compressed)
        try:
            xyz = pc.copy_data("xyz")
            rgba = pc.copy_data("rgba")
            if xyz is None or xyz.ndim != 3 or xyz.shape[2] < 3:
                raise RuntimeError(
                    f"Unexpected xyz shape: {None if xyz is None else xyz.shape}"
                )
            mask = np.isfinite(xyz).all(axis=2)
            pts = xyz[mask].reshape(-1, 3)
            cols = None
            if rgba is not None and rgba.ndim == 3 and rgba.shape[2] >= 3:
                cols = (rgba[mask][:, :3].astype(np.float32) / 255.0)
            cloud = o3d.geometry.PointCloud()
            cloud.points = o3d.utility.Vector3dVector(pts)
            if cols is not None and len(cols) == len(pts):
                cloud.colors = o3d.utility.Vector3dVector(cols)
            o3d.io.write_point_cloud(
                str(p), cloud, write_ascii=False, compressed=True
            )
            written["pc"] = p
            saved = True
        except Exception as e:
            ply_errors.append(f"open3d path: {type(e).__name__}: {e}")

        # Fallback: try Zivid's native PLY save methods
        if not saved:
            try:
                pc.save(str(p))
                written["pc"] = p
                saved = True
            except Exception as e:
                ply_errors.append(f"pc.save: {type(e).__name__}: {e}")

        if not saved:
            try:
                frame.save(str(p))
                written["pc"] = p
                saved = True
            except Exception as e:
                ply_errors.append(f"frame.save: {type(e).__name__}: {e}")

        if not saved:
            raise RuntimeError(
                "Zivid: failed saving PLY. Errors tried:\n  - "
                + "\n  - ".join(ply_errors)
            )

    return written, depth_meta
