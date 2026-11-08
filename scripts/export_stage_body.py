"""Build Stage's figure from MHR: `forge_studio/frontend/stage/body.json` + `body.bin`.

A developer tool, run once per change to the figure; the output ships with Studio,
so nobody else needs MHR's 700 MB model. Review: `Evidence/source-review/ST1-stage.md`.

    venv/Scripts/python.exe scripts/export_stage_body.py <path to mhr_model.pt>

What it writes, all in metres, MHR's axes (+Y up, the figure faces +Z):
- the rest mesh of MHR LOD 1 (18,439 vertices, 36,874 faces) for Stage's body
  shape, posed at zero with pose corrections OFF;
- the skeleton: 127 joints, each with its parent and its rest position and
  rotation (quaternion x, y, z, w);
- skin weights: MHR's own, at most 4 joints per vertex (MHR never uses more);
- presets: whole-body poses authored with MHR's named controls, stored as each
  joint's LOCAL rotation, so MHR's twist helper bones come out exactly.

Plain skinning of this data reproduces MHR's own output with corrections off to
within 0.001 mm (`Evidence/controlnet-expansion-2026-11-06/e1/verify_stage_body.py`).
MHR is Apache-2.0; its licence ships beside the files as `LICENSE-MHR.txt`.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import torch

APP = Path(__file__).resolve().parents[1]
OUT = APP / "forge_studio" / "frontend" / "stage"

#: Stage's default body (owner, 2026-11-07: "slim neutral"). MHR identity
#: coefficient 0 runs thin (-) to heavy (+), coefficient 1 broad (-) to curvy (+).
IDENTITY = {0: -1.5, 1: 0.6}

#: Arms hanging at the sides. Solved, not guessed: from MHR's A-pose, uparm_ry
#: alone also swings the arm forward, so uparm_rz pulls it back. These put the
#: wrist 0.50 m below the shoulder, 5 cm out and 1 cm forward. Presets swing an
#: arm forward or back by adding to ARM_HANG_RZ.
ARM_HANG_RY, ARM_HANG_RZ = -32.5, -40.0
ARMS_DOWN = {"l_uparm_ry": ARM_HANG_RY, "r_uparm_ry": ARM_HANG_RY,
             "l_uparm_rz": ARM_HANG_RZ, "r_uparm_rz": ARM_HANG_RZ,
             "l_elbow_bend": 8, "r_elbow_bend": 8}

#: Presets as {MHR pose control: degrees}. Signs, both sides alike, read from
#: front and side renders: upleg_rz - leg FORWARD (MHR allows -143 forward,
#: +57 back), upleg_ry - leg out, uparm_rz + arm forward, uparm_ry + arm out and
#: up from MHR's A-pose, knee_bend / elbow_bend + bends, spine_bend + forward.
#: Values outside MHR's own limits are refused, not clamped.
PRESETS: dict[str, dict[str, float]] = {
    "standing": {**ARMS_DOWN},
    # Opposite arm and leg swing together, as people walk.
    "walking": {**ARMS_DOWN, "l_upleg_rz": -24, "l_knee_bend": 10, "r_upleg_rz": 14,
                "r_knee_bend": 28, "l_uparm_rz": ARM_HANG_RZ - 18, "r_uparm_rz": ARM_HANG_RZ + 20,
                "l_elbow_bend": 14, "r_elbow_bend": 26},
    "running": {**ARMS_DOWN, "l_upleg_rz": -55, "l_knee_bend": 75, "r_upleg_rz": 25,
                "r_knee_bend": 95, "l_uparm_rz": ARM_HANG_RZ - 25, "r_uparm_rz": ARM_HANG_RZ + 45,
                "l_elbow_bend": 85, "r_elbow_bend": 85, "spine_bend0": 12},
    "sitting": {**ARMS_DOWN, "l_upleg_rz": -88, "r_upleg_rz": -88, "l_knee_bend": 88,
                "r_knee_bend": 88, "l_uparm_rz": ARM_HANG_RZ + 30, "r_uparm_rz": ARM_HANG_RZ + 30,
                "l_elbow_bend": 45, "r_elbow_bend": 45, "spine_bend0": 5},
    "kneeling": {**ARMS_DOWN, "l_upleg_rz": -85, "l_knee_bend": 95, "r_upleg_rz": 10,
                 "r_knee_bend": 105, "l_uparm_rz": ARM_HANG_RZ + 35, "l_elbow_bend": 55,
                 "spine_bend0": 10},
    "arms_up": {"l_uparm_ry": 68, "r_uparm_ry": 68, "l_clavicle_ry": 25, "r_clavicle_ry": 25,
                "l_elbow_bend": 20, "r_elbow_bend": 20},
}


#: ST6. Body types (owner, 2026-11-08: SFW art and fan art of younger
#: characters). MHR is built from adult scans, so each type is built from:
#: `identity` -- MHR identity coefficients: 0 thin/heavy, 1 broad/curvy, and
#:   20/21 from MHR's head block (20 negative = a smaller, finer face, 21
#:   negative = rounder), probed in `e1/probe_head_identity.py`;
#: `shares` -- a multiple of MHR's own MINIMUM for a scale control (1 = MHR's
#:   minimum, 2 = twice past it, negative = wider/longer than the adult);
#: `thin` -- limbs, neck and clavicles pulled toward their bone's axis (1 = as
#:   is), baked into the rest mesh; joints stay where MHR put them;
#: `head` -- a scale on the head joint, applied in the browser, where the
#:   neck's skin weights blend it;
#: `height` -- standing height in metres, by scaling the whole figure.
#: ST6c (owner, 2026-11-08, of ST6b): the children read as small ADULTS, a
#: full adult trunk on shortened limbs, with a heavier body ("shoulders a bit
#: broad, hips very narrow"). Children are proportioned more like a slim,
#: scaled-down body with a big head, narrow shoulders, a straight torso and
#: thin limbs, so the limbs keep most of their length and the widths change.
#: Probed in `e1/probe_child_bodies.py` (renders `e1/renders/st6c_*.png`).
BODIES = {
    "adult":       {"identity": {0: -1.5, 1: 0.6}, "shares": {}, "thin": {}, "head": 1.0, "height": None},
    "teen":        {"identity": {0: -1.8, 1: 0.2, 20: -1.5},
                    "shares": {"scale_shoulder_width": 1.2, "scale_hip_width": -0.1, "scale_neck_length": 0.2},
                    "thin": {"limb": 0.92, "neck": 0.9, "shoulder": 0.9}, "head": 1.05, "height": 1.60},
    "child":       {"identity": {0: -2.2, 1: 0.0, 20: -1.5},
                    "shares": {"scale_shoulder_width": 2.0, "scale_hip_width": -0.25, "scale_neck_length": 0.2},
                    "thin": {"limb": 0.82, "neck": 0.8, "shoulder": 0.8}, "head": 1.24, "height": 1.30},
    "small_child": {"identity": {0: -2.0, 1: 0.0, 20: -2.0, 21: -1.0},
                    "shares": {"scale_spine_length": 0.4, "scale_uplegs": 0.6, "scale_lowlegs": 0.6,
                               "scale_uparms": 0.4, "scale_lowarms": 0.4, "scale_neck_length": 0.3,
                               "scale_shoulder_width": 2.4, "scale_hip_width": 0.0},
                    "thin": {"limb": 0.85, "neck": 0.8, "shoulder": 0.8}, "head": 1.43, "height": 1.00},
}

#: Bones `thin` acts on: (joint, joint at its far end, which `thin` factor).
#: A bone's weight is its joint's subtree minus the far joint's subtree, so
#: MHR's twist helpers (the joint's own children) count as the bone.
THIN_BONES = [("l_upleg", "l_lowleg", "limb"), ("l_lowleg", "l_foot", "limb"),
              ("r_upleg", "r_lowleg", "limb"), ("r_lowleg", "r_foot", "limb"),
              ("l_uparm", "l_lowarm", "limb"), ("l_lowarm", "l_wrist_twist", "limb"),
              ("r_uparm", "r_lowarm", "limb"), ("r_lowarm", "r_wrist_twist", "limb"),
              ("c_neck", "c_head", "neck"),
              ("l_clavicle", "l_uparm", "shoulder"), ("r_clavicle", "r_uparm", "shoulder")]


def _subtree_weight(root, parents, skin_joint, skin_weight):
    members = {root}
    for j, parent in enumerate(parents):     # MHR lists parents before children
        if parent in members:
            members.add(j)
    return np.where(np.isin(skin_joint, list(members)), skin_weight, 0).sum(axis=1)


def _thin(vertices, joints_m, joint_names, parents, skin_joint, skin_weight, factors):
    """Pull each bone's skin toward the bone's axis by its `factors` entry."""
    out = vertices.copy()
    for start, end, kind in THIN_BONES:
        factor = factors.get(kind, 1.0)
        if factor == 1.0:
            continue
        a, b = joint_names.index(start), joint_names.index(end)
        weight = np.clip(_subtree_weight(a, parents, skin_joint, skin_weight)
                         - _subtree_weight(b, parents, skin_joint, skin_weight), 0, 1)
        axis = (joints_m[b] - joints_m[a]) / np.linalg.norm(joints_m[b] - joints_m[a])
        rel = vertices - joints_m[a]
        perp = rel - (rel @ axis)[:, None] * axis
        out -= (1.0 - factor) * weight[:, None] * perp
    return out


def _evaluate(model, names, identity, pose, raw=None):
    values = torch.zeros(1, len(names) - identity.shape[1])
    limits = model.get_parameter_limits().detach().numpy()
    for name, value in (raw or {}).items():
        values[0, names.index(name)] = float(value)
    for name, degrees in pose.items():
        index = names.index(name)
        low, high = limits[index]
        radians = math.radians(degrees)
        if high > low and not low - 1e-6 <= radians <= high + 1e-6:
            raise SystemExit(f"{name} {degrees} is outside MHR's limits "
                             f"{math.degrees(low):.1f}..{math.degrees(high):.1f}")
        values[0, index] = radians
    with torch.inference_mode():
        vertices, skeleton = model(identity_coeffs=identity, model_parameters=values,
                                   face_expr_coeffs=torch.zeros(1, 72), apply_correctives=False)
    return vertices[0].double().numpy() / 100.0, skeleton[0].double().numpy()


def _q_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz])


def _q_conj(q):
    return np.array([-q[0], -q[1], -q[2], q[3]])


def _local_rotations(world_quats, parents):
    local = []
    for j, parent in enumerate(parents):
        q = world_quats[j] if parent < 0 else _q_mul(_q_conj(world_quats[parent]), world_quats[j])
        local.append(q / np.linalg.norm(q))
    return np.array(local)


def export(model_path: Path) -> dict:
    model = torch.jit.load(str(model_path), map_location="cpu").eval()
    names = list(model.get_parameter_names())
    identity = torch.zeros(1, int(model.get_num_identity_blendshapes()))
    for index, value in IDENTITY.items():
        identity[0, index] = value
    joint_names = list(model.get_joint_names())
    parents = [int(p) for p in model.character_torch.skeleton.joint_parents]
    faces = model.character_torch.mesh.faces.numpy().astype(np.int64)

    vertices, skeleton = _evaluate(model, names, identity, {})
    if skeleton[:, 7].min() < 0.999 or skeleton[:, 7].max() > 1.001:
        raise SystemExit("MHR joint scales are not 1; Stage's skinning assumes they are")

    # Faces wound outward, so three.js's own normals face out.
    corners = vertices[faces]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    outward = ((corners.mean(axis=1) - vertices.mean(axis=0)) * normals).sum(axis=1)
    if (outward < 0).mean() > 0.5:
        faces = faces[:, ::-1].copy()

    joint_index, joint_weight = (t.numpy() for t in model.get_lbsw())
    order = np.argsort(joint_weight, axis=1)[:, ::-1][:, :4]
    skin_index = np.take_along_axis(joint_index, order, axis=1).astype(np.uint8)
    skin_weight = np.take_along_axis(joint_weight, order, axis=1).astype(np.float64)
    skin_weight /= skin_weight.sum(axis=1, keepdims=True)
    skin_index[skin_weight == 0] = 0

    rest_local = _local_rotations(skeleton[:, 3:7], parents)
    presets = {}
    for preset, pose in PRESETS.items():
        _, posed = _evaluate(model, names, identity, pose)
        local = _local_rotations(posed[:, 3:7], parents)
        changed = {}
        for j, (q, r) in enumerate(zip(local, rest_local)):
            if abs(abs(float(np.dot(q, r))) - 1.0) > 1e-7:
                changed[joint_names[j]] = [round(float(x), 6) for x in q]
        presets[preset] = {"rotations": changed}

    # ST6. Each body type: its own rest mesh and joints (MHR's scales baked
    # in; joint scales stay 1, checked), its head scale, and the whole-figure
    # scale that brings it to its height with that head.
    limits = model.get_parameter_limits().detach().numpy()
    head_joint = joint_names.index("c_head")
    lbs_index, lbs_weight = (t.numpy() for t in model.get_lbsw())
    bodies, body_blobs = {}, []
    for body, spec in BODIES.items():
        ident = torch.zeros_like(identity)
        for index, value in spec["identity"].items():
            ident[0, index] = value
        raw = {key: limits[names.index(key)][0] * share for key, share in spec["shares"].items()}
        verts, skel = _evaluate(model, names, ident, {}, raw)
        verts = _thin(verts, skel[:, :3] / 100.0, joint_names, parents, lbs_index, lbs_weight, spec["thin"])
        if skel[:, 7].min() < 0.999 or skel[:, 7].max() > 1.001:
            raise SystemExit(f"{body}: MHR joint scales are not 1")
        if np.abs(_local_rotations(skel[:, 3:7], parents) - rest_local).max() > 1e-4:
            raise SystemExit(f"{body}: rest rotations differ from the adult's; presets would not carry over")
        crown, floor = verts[:, 1].max(), verts[:, 1].min()
        grown = (crown - floor) + (crown - skel[head_joint, 1] / 100.0) * (spec["head"] - 1.0)
        factor = 1.0 if spec["height"] is None else spec["height"] / grown
        head = (crown - skel[joint_names.index("c_jaw"), 1] / 100.0) * 1.25 * spec["head"]
        bodies[body] = {
            "buffer": "position" if body == "adult" else f"position.{body}",
            "head": spec["head"], "scale": round(float(factor), 5),
            # The rest mesh's lowest point: MHR keeps the hips' height when the
            # legs shorten, so a shorter body's soles float unless the browser
            # puts the hips this far above the floor (times the figure's scale).
            "floor": round(float(floor), 6),
            "joints": [[round(float(x), 6) for x in skel[j, :3] / 100.0] for j in range(len(joint_names))],
            "headsTall": round(float(grown / head), 2),
        }
        if body != "adult":
            body_blobs.append((f"position.{body}", verts.astype("<f4")))
        print(f"  {body:12s} {grown * factor:.2f} m tall, about {grown / head:.1f} heads")

    blobs = [
        ("position", vertices.astype("<f4")),
        ("skinWeight", skin_weight.astype("<f4")),
        ("index", faces.astype("<u2")),
        ("skinIndex", skin_index),
        *body_blobs,
    ]
    layout, offset, payload = {}, 0, b""
    for name, array in blobs:
        data = np.ascontiguousarray(array).tobytes()
        layout[name] = {"offset": offset, "length": int(array.size), "type": str(array.dtype)}
        payload += data
        offset += len(data)

    meta = {
        "version": 1,
        "source": ("MHR v1.0.1 (facebookresearch/MHR), LOD 1. Copyright (c) Meta Platforms, Inc. "
                   "and affiliates; Apache-2.0, see LICENSE-MHR.txt. Modified by Forge Studio: "
                   "exported by scripts/export_stage_body.py, reshaped (identity) and posed (presets)."),
        "identity": {str(k): v for k, v in IDENTITY.items()},
        "units": "metres",
        "vertexCount": int(vertices.shape[0]),
        "faceCount": int(faces.shape[0]),
        "buffers": layout,
        "joints": [{"name": n, "parent": parents[j],
                    "position": [round(float(x), 6) for x in skeleton[j, :3] / 100.0],
                    "quaternion": [round(float(x), 7) for x in skeleton[j, 3:7]]}
                   for j, n in enumerate(joint_names)],
        "presets": presets,
        "bodies": bodies,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "body.bin").write_bytes(payload)
    (OUT / "body.json").write_bytes(json.dumps(meta, separators=(",", ":")).encode("utf-8"))
    return meta


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    result = export(Path(sys.argv[1]))
    print(f"wrote {OUT}: {result['vertexCount']} vertices, {result['faceCount']} faces, "
          f"{len(result['joints'])} joints, presets {', '.join(result['presets'])}")
