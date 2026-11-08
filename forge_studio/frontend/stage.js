/**
 * Forge Studio — Stage module
 * by ToxicHost & Moritz
 *
 * Phase 1 (2026-11-07): one posable 3D figure that becomes a ControlNet unit's
 * picture. Pose it in the Stage tab; a unit whose source is "Stage" renders the
 * current scene at the picture's size when Generate is pressed.
 * Review: Evidence/source-review/ST1-stage.md
 *
 * The figure is MHR LOD 1 (Apache-2.0, stage/LICENSE-MHR.txt), exported once by
 * scripts/export_stage_body.py into stage/body.json + body.bin: rest mesh,
 * 127-joint skeleton, MHR's own skin weights, presets as local joint rotations.
 * Plain skinning of that data matches MHR's output (pose corrections off) to
 * 0.002 mm. three.js 0.180 (MIT, stage/three/) is imported only when Stage is
 * first needed, so it costs nothing for anyone who never uses it.
 *
 * The map is rendered with the recipe anima_3dpose_6200 was trained on
 * (toyxyz booru3dpose_10k SOURCES.md): one solid colour on RGB black, smooth
 * shading, light relative to the camera, key 35 deg to the side and up,
 * ambient 0.25, key diffuse 0.65, fill 0.13, specular 0.22, shininess 40, no
 * shadows. The same equations as Evidence/.../e1/render_pose.py, which drove
 * the owner's E1 runs.
 *
 * Registers via StudioModules.register("stage", {...}); the ControlNet panel
 * reads window.StudioStage.
 */
(function () {
"use strict";
var TAG = "[Stage]", VERSION = "1.0.0";
var BASE = "/studio/static/stage/";
var STORE_KEY = "studio.stage.v1";

function _t(key, fallback, params) {
  return (window.I18N && window.I18N.t) ? window.I18N.t(key, fallback, params) : fallback;
}

// ── the recipe ───────────────────────────────────────────────────────────
var RECIPE = {
  base: [0.86, 0.71, 0.43],          // close to the published sample's tan
  keyAzimuth: 35, keyElevation: 35,  // training drew both from 25..45 deg
  fillAzimuth: -17.5, fillElevation: 0,
};
var HIGHLIGHT = [0.48, 0.56, 1.0];   // Studio's accent, on the selected part only

// Clickable body parts. Every joint belongs to the nearest one above it, so
// MHR's twist helpers, fingers, jaw and eyes move with their limb. "l_" is the
// figure's own left.
var PARTS = [
  { joint: "root",     key: "stage.part.hips",       label: "Hips (whole body)" },
  { joint: "c_spine1", key: "stage.part.spine",      label: "Spine" },
  { joint: "c_spine3", key: "stage.part.chest",      label: "Chest" },
  { joint: "c_neck",   key: "stage.part.neck",       label: "Neck" },
  { joint: "c_head",   key: "stage.part.head",       label: "Head" },
  { joint: "l_uparm",  key: "stage.part.lUpperArm",  label: "Left upper arm" },
  { joint: "l_lowarm", key: "stage.part.lForearm",   label: "Left forearm" },
  { joint: "l_wrist",  key: "stage.part.lHand",      label: "Left hand" },
  { joint: "r_uparm",  key: "stage.part.rUpperArm",  label: "Right upper arm" },
  { joint: "r_lowarm", key: "stage.part.rForearm",   label: "Right forearm" },
  { joint: "r_wrist",  key: "stage.part.rHand",      label: "Right hand" },
  { joint: "l_upleg",  key: "stage.part.lThigh",     label: "Left thigh" },
  { joint: "l_lowleg", key: "stage.part.lShin",      label: "Left shin" },
  { joint: "l_foot",   key: "stage.part.lFoot",      label: "Left foot" },
  { joint: "r_upleg",  key: "stage.part.rThigh",     label: "Right thigh" },
  { joint: "r_lowleg", key: "stage.part.rShin",      label: "Right shin" },
  { joint: "r_foot",   key: "stage.part.rFoot",      label: "Right foot" },
];

var PRESETS = [
  { id: "standing", key: "stage.preset.standing", label: "Standing" },
  { id: "walking",  key: "stage.preset.walking",  label: "Walking" },
  { id: "running",  key: "stage.preset.running",  label: "Running" },
  { id: "sitting",  key: "stage.preset.sitting",  label: "Sitting" },
  { id: "kneeling", key: "stage.preset.kneeling", label: "Kneeling" },
  { id: "arms_up",  key: "stage.preset.armsUp",   label: "Arms up" },
];

var VERTEX = [
  "#include <common>",
  "#include <skinning_pars_vertex>",
  "attribute float partId;",
  "varying vec3 vNormalView;",
  "varying vec3 vViewPos;",
  "varying float vPart;",
  "void main() {",
  "  #include <beginnormal_vertex>",
  "  #include <skinbase_vertex>",
  "  #include <skinnormal_vertex>",
  "  #include <defaultnormal_vertex>",
  "  #include <begin_vertex>",
  "  #include <skinning_vertex>",
  "  #include <project_vertex>",
  "  vNormalView = normalize(transformedNormal);",
  "  vViewPos = -mvPosition.xyz;",
  "  vPart = partId;",
  "}",
].join("\n");

// Written straight out, no colour-space conversion: the numbers are the
// recipe's, as render_pose.py writes them.
var FRAGMENT = [
  "uniform vec3 baseColor;",
  "uniform vec3 keyDir;",
  "uniform vec3 fillDir;",
  "uniform float highlightPart;",
  "uniform vec3 highlightColor;",
  "varying vec3 vNormalView;",
  "varying vec3 vViewPos;",
  "varying float vPart;",
  "void main() {",
  "  vec3 n = normalize(vNormalView);",
  "  vec3 v = normalize(vViewPos);",
  "  if (dot(n, v) < 0.0) n = -n;",
  "  float dk = max(dot(n, keyDir), 0.0);",
  "  float df = max(dot(n, fillDir), 0.0);",
  "  vec3 r = reflect(-keyDir, n);",
  "  float spec = dk > 0.0 ? pow(max(dot(r, v), 0.0), 40.0) : 0.0;",
  "  vec3 base = baseColor;",
  "  if (highlightPart >= 0.0 && abs(vPart - highlightPart) < 0.5) base = mix(base, highlightColor, 0.55);",
  "  vec3 c = base * (0.25 + 0.65 * dk + 0.13 * df) + 0.22 * spec;",
  "  gl_FragColor = vec4(clamp(c, 0.0, 1.0), 1.0);",
  "}",
].join("\n");

function _lightDir(azimuth, elevation) {
  var a = azimuth * Math.PI / 180, e = elevation * Math.PI / 180;
  return [Math.sin(a) * Math.cos(e), Math.sin(e), Math.cos(a) * Math.cos(e)];
}

// Lens as a full-frame focal length; the field of view is vertical.
function _fovFromFocal(mm) { return 2 * Math.atan(12 / mm) * 180 / Math.PI; }

// ── state ────────────────────────────────────────────────────────────────
var T = null, OrbitControls = null, TransformControls = null;
var _ready = null;        // Promise, once loading has started
var _failed = null;       // the error, if loading failed
var _meta = null;
var _renderer = null, _scene = null, _mesh = null, _bones = [], _boneByName = {};
var _restLocal = [];      // THREE.Quaternion per joint
var _restWorld = [];      // THREE.Quaternion per joint
var _partOfJoint = [];    // joint index -> part index
var _camera = null, _orbit = null, _gizmo = null, _grid = null;
var _material = null;
var _focal = 35;
var _selected = -1;
var _els = {};
var _container = null, _viewport = null, _frameEl = null;
var _active = false, _raf = 0, _saveTimer = 0;
var _targets = {};        // reusable render targets by "w x h"

function _picture() {
  var w = parseInt(document.getElementById("paramWidth")?.value, 10) || 1024;
  var h = parseInt(document.getElementById("paramHeight")?.value, 10) || 1024;
  return { width: Math.max(64, w), height: Math.max(64, h) };
}

// ── figures ──────────────────────────────────────────────────────────────
// ST2. Up to four figures share one mesh and the rest pose; each has its own
// bones, colour and material. `_mesh`, `_bones`, `_boneByName` and `_material`
// always point at the CURRENT figure, so posing, handles, presets and Mirror
// act on whichever figure was last clicked or chosen in the list.
// Distinct solid colours, as the 3D-pose adapter saw several people drawn
// ("distinct colors within the same image"); the first is the sample's tan.
var FIGURE_COLORS = [[0.86, 0.71, 0.43], [0.55, 0.62, 0.92], [0.62, 0.80, 0.52], [0.90, 0.60, 0.62]];
var MAX_FIGURES = 4;
var RING_INNER = 0.30, RING_OUTER = 0.36, FLOOR_Y = 0.004;
var _figures = [], _current = 0, _geometry = null, _restMatrices = [];
var _bodies = {};           // ST6: body type name -> { geometry, restMatrices, head, scale, rootRest }
var BODY_TYPES = [
  { id: "adult",       key: "stage.body.adult",      label: "Adult" },
  { id: "teen",        key: "stage.body.teen",       label: "Teen" },
  { id: "child",       key: "stage.body.child",      label: "Child" },
  { id: "small_child", key: "stage.body.smallChild", label: "Small child" },
];

// Bones and skinned mesh for one body type, at its rest pose, bound to it.
function _makeBody(body, material) {
  var joints = _meta.joints, rest = _bodies[body].restMatrices;
  var bones = joints.map(function (j, index) {
    var bone = new T.Bone();
    bone.name = j.name;
    var local = j.parent < 0 ? rest[index].clone()
      : new T.Matrix4().copy(rest[j.parent]).invert().multiply(rest[index]);
    local.decompose(bone.position, bone.quaternion, bone.scale);
    return bone;
  });
  var byName = {};
  bones.forEach(function (b) { byName[b.name] = b; });
  var roots = [];
  joints.forEach(function (j, index) {
    if (j.parent < 0) roots.push(bones[index]);
    else bones[j.parent].add(bones[index]);
  });
  roots.forEach(function (b) { b.updateMatrixWorld(true); });
  var mesh = new T.SkinnedMesh(_bodies[body].geometry, material);
  roots.forEach(function (b) { mesh.add(b); });
  mesh.bind(new T.Skeleton(bones));
  mesh.frustumCulled = false;
  return { mesh: mesh, bones: bones, byName: byName };
}

// The body's head and whole-figure scales, the figure's height on top. The
// hips' height scales with it, so the feet stay on the floor; any crouch the
// owner made is kept.
function _applyBodyScale(figure, oldScale) {
  var body = _bodies[figure.body] || _bodies.adult;
  var scale = body.scale * (figure.height || 1);
  figure.boneByName.c_head.scale.setScalar(body.head);
  var root = figure.boneByName.root;
  var restY = body.rootRest.y - (body.floor || 0);
  var offset = oldScale ? root.position.y - restY * oldScale : 0;
  root.scale.setScalar(scale);
  root.position.y = restY * scale + offset;
  figure.scale = scale;
  figure.mesh.updateMatrixWorld(true);
}

function _addFigure(initial, bodyName) {
  if (_figures.length >= MAX_FIGURES) return null;
  var body = _bodies[bodyName] ? bodyName : "adult";

  // A colour no other figure has.
  var used = _figures.map(function (f) { return f.colorIndex; });
  var colorIndex = 0;
  while (used.indexOf(colorIndex) >= 0) colorIndex++;
  var rgb = FIGURE_COLORS[colorIndex % FIGURE_COLORS.length];
  var key = _lightDir(RECIPE.keyAzimuth, RECIPE.keyElevation);
  var fill = _lightDir(RECIPE.fillAzimuth, RECIPE.fillElevation);
  var material = new T.ShaderMaterial({
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    side: T.DoubleSide,
    uniforms: {
      baseColor: { value: new T.Color().setRGB(rgb[0], rgb[1], rgb[2], T.LinearSRGBColorSpace) },
      keyDir: { value: new T.Vector3().fromArray(key) },
      fillDir: { value: new T.Vector3().fromArray(fill) },
      highlightPart: { value: -1 },
      highlightColor: { value: new T.Vector3().fromArray(HIGHLIGHT) },
    },
  });
  var made = _makeBody(body, material);
  var mesh = made.mesh, bones = made.bones, byName = made.byName;
  _scene.add(mesh);
  // ST4. A ring on the floor to slide the figure, a knob on it to turn it.
  var tint = new T.Color().setRGB(rgb[0], rgb[1], rgb[2], T.LinearSRGBColorSpace);
  var ring = new T.Mesh(new T.RingGeometry(RING_INNER, RING_OUTER, 48),
    new T.MeshBasicMaterial({ color: tint, transparent: true, opacity: 0.55, side: T.DoubleSide, depthWrite: false }));
  ring.rotation.x = -Math.PI / 2;
  ring.renderOrder = 5;
  var knob = new T.Mesh(new T.SphereGeometry(0.045, 16, 12),
    new T.MeshBasicMaterial({ color: tint, depthTest: false, transparent: true, opacity: 0.95 }));
  knob.renderOrder = 10;
  _scene.add(ring);
  _scene.add(knob);
  var figure = { mesh: mesh, bones: bones, boneByName: byName, material: material, colorIndex: colorIndex,
                 ring: ring, knob: knob, body: body, height: 1, scale: 1 };
  _applyBodyScale(figure, 0);
  _figures.push(figure);
  _setCurrent(_figures.length - 1);
  if (!initial) {
    // A new figure stands beside the others, in the standing pose.
    applyPreset("standing");
    var n = _figures.length - 1;
    _boneByName.root.position.x += (n % 2 ? 1 : -1) * 0.7 * Math.ceil(n / 2);
    _changed(true);
  }
  return figure;
}

// ST6. Give a figure another body type. Its pose, place on the floor and the
// way it faces carry over; so does a crouch, in proportion.
function setBody(index, body) {
  var figure = _figures[index];
  if (!figure || !_bodies[body] || figure.body === body) return false;
  var pose = _figurePose(figure);
  var oldBody = _bodies[figure.body] || _bodies.adult;
  var crouch = (figure.boneByName.root.position.y - (oldBody.rootRest.y - (oldBody.floor || 0)) * figure.scale) / (figure.scale || 1);
  if (_gizmo) _gizmo.detach();
  _scene.remove(figure.mesh);
  var made = _makeBody(body, figure.material);
  figure.mesh = made.mesh; figure.bones = made.bones; figure.boneByName = made.byName; figure.body = body;
  _scene.add(figure.mesh);
  figure.bones.forEach(function (b) { var q = pose.rotations[b.name]; if (q) b.quaternion.fromArray(q); });
  _applyBodyScale(figure, 0);
  var root = figure.boneByName.root;
  root.position.x = pose.root[0]; root.position.z = pose.root[2];
  root.position.y += crouch * figure.scale;
  if (index === _current) _setCurrent(index);
  _renderFigureList();
  _changed(true);
  return true;
}

// ST6. A figure's height on top of its body type: 80-120%.
function setHeight(index, share) {
  var figure = _figures[index];
  if (!figure) return false;
  var old = figure.scale;
  figure.height = Math.max(0.8, Math.min(1.2, share));
  _applyBodyScale(figure, old);
  _changed(true);
  return true;
}

function _setCurrent(index) {
  if (_material) _material.uniforms.highlightPart.value = -1;
  _current = Math.max(0, Math.min(index, _figures.length - 1));
  var figure = _figures[_current];
  _mesh = figure.mesh; _bones = figure.bones; _boneByName = figure.boneByName; _material = figure.material;
  _selected = -1;
  if (_gizmo) _gizmo.detach();
  _renderFigureList();
}

function removeFigure(index) {
  if (_figures.length <= 1 || index < 0 || index >= _figures.length) return false;
  var figure = _figures[index];
  if (_gizmo) _gizmo.detach();
  _scene.remove(figure.mesh);
  _scene.remove(figure.ring);
  _scene.remove(figure.knob);
  figure.material.dispose();
  _figures.splice(index, 1);
  _setCurrent(Math.min(_current, _figures.length - 1));
  _select(-1);
  _changed(true);
  return true;
}

function _renderFigureList() {
  var list = _els.figures;
  if (!list) return;
  list.textContent = "";
  _figures.forEach(function (figure, index) {
    var row = document.createElement("button");
    row.type = "button";
    row.className = "stage-figure" + (index === _current ? " active" : "");
    var swatch = document.createElement("span");
    swatch.className = "stage-swatch";
    var rgb = FIGURE_COLORS[figure.colorIndex % FIGURE_COLORS.length];
    swatch.style.background = "rgb(" + rgb.map(function (c) { return Math.round(c * 255); }).join(",") + ")";
    var label = document.createElement("span");
    label.textContent = _t("stage.figureName", "Figure {n}", { n: index + 1 });
    row.appendChild(swatch);
    row.appendChild(label);
    row.addEventListener("click", function () { _setCurrent(index); _select(-1); _draw(); });
    list.appendChild(row);
  });
  if (_els.addFigure) _els.addFigure.disabled = _figures.length >= MAX_FIGURES;
  if (_els.removeFigure) _els.removeFigure.disabled = _figures.length <= 1;
  var current = _figures[_current];
  if (current && _els.body) _els.body.value = current.body;
  if (current && _els.height) {
    _els.height.value = String(Math.round((current.height || 1) * 100));
    _els.heightValue.textContent = _els.height.value + "%";
  }
}

// ── loading ──────────────────────────────────────────────────────────────
function ensureReady() {
  if (_ready) return _ready;
  var v = "?v=" + VERSION;
  _ready = Promise.all([
    import(BASE + "three/three.module.min.js" + v),
    import(BASE + "three/OrbitControls.js" + v),
    import(BASE + "three/TransformControls.js" + v),
    fetch(BASE + "body.json" + v).then(function (r) { if (!r.ok) throw new Error("body.json " + r.status); return r.json(); }),
    fetch(BASE + "body.bin" + v).then(function (r) { if (!r.ok) throw new Error("body.bin " + r.status); return r.arrayBuffer(); }),
  ]).then(function (parts) {
    T = parts[0];
    OrbitControls = parts[1].OrbitControls;
    TransformControls = parts[2].TransformControls;
    _meta = parts[3];
    _build(parts[4]);
    _restore();
    console.log(TAG, "Ready:", _meta.vertexCount, "vertices,", _meta.joints.length, "joints");
    _changed(false);
  }).catch(function (err) {
    _failed = err;
    console.error(TAG, "Could not load Stage:", err);
    throw err;
  });
  return _ready;
}

function _buffer(bin, name, Type) {
  var info = _meta.buffers[name];
  return new Type(bin, info.offset, info.length);
}

function _build(bin) {
  var joints = _meta.joints;
  var geometry = new T.BufferGeometry();
  geometry.setAttribute("position", new T.BufferAttribute(_buffer(bin, "position", Float32Array), 3));
  geometry.setIndex(new T.BufferAttribute(_buffer(bin, "index", Uint16Array), 1));
  var skinIndex = _buffer(bin, "skinIndex", Uint8Array);
  var skinWeight = _buffer(bin, "skinWeight", Float32Array);
  geometry.setAttribute("skinIndex", new T.Uint16BufferAttribute(Uint16Array.from(skinIndex), 4));
  geometry.setAttribute("skinWeight", new T.BufferAttribute(skinWeight, 4));
  geometry.computeVertexNormals();

  // Joint -> clickable part, by walking up to the nearest listed joint.
  var partByJoint = {};
  PARTS.forEach(function (p, i) { partByJoint[p.joint] = i; });
  _partOfJoint = joints.map(function (j, index) {
    for (var k = index; k >= 0; k = joints[k].parent) {
      if (partByJoint[joints[k].name] !== undefined) return partByJoint[joints[k].name];
    }
    return 0;
  });
  var count = _meta.vertexCount, partIds = new Float32Array(count);
  for (var vtx = 0; vtx < count; vtx++) {
    var best = 0;
    for (var s = 1; s < 4; s++) if (skinWeight[vtx * 4 + s] > skinWeight[vtx * 4 + best]) best = s;
    partIds[vtx] = _partOfJoint[skinIndex[vtx * 4 + best]];
  }
  geometry.setAttribute("partId", new T.BufferAttribute(partIds, 1));

  // Rest pose, shared by every figure: local = inverse(parent world) * world.
  _restMatrices = joints.map(function (j) {
    return new T.Matrix4().compose(new T.Vector3().fromArray(j.position),
      new T.Quaternion().fromArray(j.quaternion), new T.Vector3(1, 1, 1));
  });
  joints.forEach(function (j, index) {
    var local = j.parent < 0 ? _restMatrices[index].clone()
      : new T.Matrix4().copy(_restMatrices[j.parent]).invert().multiply(_restMatrices[index]);
    var p = new T.Vector3(), q = new T.Quaternion(), s = new T.Vector3();
    local.decompose(p, q, s);
    _restLocal.push(q);
    _restWorld.push(new T.Quaternion().fromArray(j.quaternion));
  });
  _geometry = geometry;

  // ST6. Body types: each its own rest mesh and joint positions, sharing the
  // faces, skin and part ids; head and whole-figure scales applied to bones.
  // An older body file without types has the adult only.
  var specs = _meta.bodies || { adult: { buffer: "position", head: 1, scale: 1, joints: null } };
  Object.keys(specs).forEach(function (name) {
    var spec = specs[name], g = geometry;
    if (spec.buffer !== "position") {
      g = new T.BufferGeometry();
      g.setAttribute("position", new T.BufferAttribute(_buffer(bin, spec.buffer, Float32Array), 3));
      g.setIndex(geometry.index);
      ["skinIndex", "skinWeight", "partId"].forEach(function (a) { g.setAttribute(a, geometry.getAttribute(a)); });
      g.computeVertexNormals();
    }
    var matrices = joints.map(function (j, index) {
      var p = spec.joints ? spec.joints[index] : j.position;
      return new T.Matrix4().compose(new T.Vector3().fromArray(p), new T.Quaternion().fromArray(j.quaternion),
        new T.Vector3(1, 1, 1));
    });
    var rootIndex = joints.findIndex(function (j) { return j.name === "root"; });
    _bodies[name] = { geometry: g, restMatrices: matrices, head: spec.head || 1, scale: spec.scale || 1, floor: spec.floor || 0,
                      rootRest: new T.Vector3().setFromMatrixPosition(matrices[rootIndex]),
                      // ST7. The lowest joint standing at rest, for setting a read pose down
                      // (MHR's `body_world`, the one parentless joint, sits at the origin: not a body part).
                      lowest: Math.min.apply(null, matrices.filter(function (m, i) { return joints[i].parent >= 0; })
                        .map(function (m) { return m.elements[13]; })) };
  });

  _scene = new T.Scene();
  _addFigure(true);
  _grid = new T.GridHelper(4, 16, 0x3a3a42, 0x26262c);
  _scene.add(_grid);

  _camera = new T.PerspectiveCamera(_fovFromFocal(_focal), 1, 0.05, 100);
  _camera.position.set(0, 1.0, 4.2);

  var canvas = document.createElement("canvas");
  canvas.className = "stage-canvas";
  _renderer = new T.WebGLRenderer({ canvas: canvas, antialias: true });
  _renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  _renderer.outputColorSpace = T.LinearSRGBColorSpace;

  _orbit = new OrbitControls(_camera, canvas);
  _orbit.target.set(0, 0.95, 0);
  _orbit.enableDamping = false;
  _orbit.addEventListener("change", function () { _changed(true); });

  _gizmo = new TransformControls(_camera, canvas);
  _gizmo.setMode("rotate");
  _gizmo.setSpace("local");
  _gizmo.setSize(0.8);
  _gizmo.addEventListener("dragging-changed", function (e) { _orbit.enabled = !e.value; });
  _gizmo.addEventListener("objectChange", function () { _changed(true); });
  _scene.add(_gizmo.getHelper());

  _buildHandles();
  // Capture phase: a press on a handle must reach no camera or gizmo control.
  canvas.addEventListener("pointerdown", _onHandleDown, true);
  canvas.addEventListener("pointerdown", _onPointerDown);
  canvas.addEventListener("pointerup", _onPointerUp);
  _frameFigure();
}

// ── drag-to-pose handles ─────────────────────────────────────────────────
// Hands and feet: drag one and its limb solves to reach it (two-bone IK),
// bending only the way it already bends. Hips: drag to move the body while the
// feet stay where they are. Handles show in the viewport, never in the map.
var LIMBS = {
  l_wrist: { upper: "l_uparm", lower: "l_lowarm", bend: -1 },
  r_wrist: { upper: "r_uparm", lower: "r_lowarm", bend: -1 },
  l_foot:  { upper: "l_upleg", lower: "l_lowleg", bend: 1 },
  r_foot:  { upper: "r_upleg", lower: "r_lowleg", bend: 1 },
};
var _handles = [];   // { mesh, joint }
var _drag = null;    // the drag in progress

function _buildHandles() {
  var geometry = new T.SphereGeometry(0.032, 16, 12);
  ["l_wrist", "r_wrist", "l_foot", "r_foot", "root"].forEach(function (joint) {
    var material = new T.MeshBasicMaterial({
      color: joint === "root" ? 0xd4537e : 0x7b8fff, depthTest: false, transparent: true, opacity: 0.85,
    });
    var mesh = new T.Mesh(geometry, material);
    mesh.renderOrder = 10;
    mesh.userData.joint = joint;
    _scene.add(mesh);
    _handles.push({ mesh: mesh, joint: joint });
  });
}

function _placeHandles() {
  _mesh.updateMatrixWorld(true);
  _handles.forEach(function (h) { _boneByName[h.joint].getWorldPosition(h.mesh.position); });
  _figures.forEach(function (f) {
    f.mesh.updateMatrixWorld(true);
    var hips = _worldPos(f.boneByName.root);
    f.ring.position.set(hips.x, FLOOR_Y, hips.z);
    var facing = _facing(f);
    f.knob.position.set(hips.x + facing.x * (RING_OUTER + 0.04), FLOOR_Y, hips.z + facing.z * (RING_OUTER + 0.04));
  });
}

// Which way a figure faces on the floor: +Z at rest, turned with its hips.
function _facing(figure) {
  var rootIndex = figure.bones.indexOf(figure.boneByName.root);
  var turned = _worldQuat(figure.boneByName.root).multiply(_restWorld[rootIndex].clone().invert());
  var dir = new T.Vector3(0, 0, 1).applyQuaternion(turned);
  dir.y = 0;
  return dir.lengthSq() > 1e-8 ? dir.normalize() : new T.Vector3(0, 0, 1);
}

function _worldQuat(bone) { return bone.getWorldQuaternion(new T.Quaternion()); }
function _worldPos(bone) { return bone.getWorldPosition(new T.Vector3()); }

// Give `bone` this world rotation, keeping its parent where it is.
function _setWorldQuat(bone, world) {
  var parent = bone.parent ? _worldQuat(bone.parent) : new T.Quaternion();
  bone.quaternion.copy(parent.invert().multiply(world));
  bone.updateMatrixWorld(true);
}

// Rotate `bone` in world space so the direction `from` turns into `to`.
function _turn(bone, from, to) {
  if (from.lengthSq() < 1e-12 || to.lengthSq() < 1e-12) return;
  var q = new T.Quaternion().setFromUnitVectors(from.clone().normalize(), to.clone().normalize());
  _setWorldQuat(bone, q.multiply(_worldQuat(bone)));
}

// Two-bone IK: put the end joint of `limb` at `target`. The knee or elbow
// stays on the side it is already bent towards; a straight limb bends towards
// the body's front (knees) or back (elbows). The end joint keeps `endWorld`,
// its world rotation from the start of the drag, so a planted foot stays flat.
function _solveLimb(endName, target, endWorld) {
  var limb = LIMBS[endName];
  var upper = _boneByName[limb.upper], lower = _boneByName[limb.lower], end = _boneByName[endName];
  _mesh.updateMatrixWorld(true);
  var a = _worldPos(upper), b = _worldPos(lower), c = _worldPos(end);
  var lenA = a.distanceTo(b), lenB = b.distanceTo(c);
  var toTarget = target.clone().sub(a);
  var d = Math.min(Math.max(toTarget.length(), Math.abs(lenA - lenB) + 1e-4), lenA + lenB - 1e-4);
  var axis = toTarget.lengthSq() > 1e-12 ? toTarget.clone().normalize() : c.clone().sub(a).normalize();
  // Which way the middle joint may go: knees towards the body's front, elbows
  // towards its back. The figure faces +Z at rest, so its front now is +Z
  // turned by how far the hips have turned from rest.
  var rootIndex = _bones.indexOf(_boneByName.root);
  var turned = _worldQuat(_boneByName.root).multiply(_restWorld[rootIndex].clone().invert());
  var natural = new T.Vector3(0, 0, limb.bend).applyQuaternion(turned);
  natural.sub(axis.clone().multiplyScalar(natural.dot(axis)));
  // Keep the bend the limb has now, unless it is straight or bent the wrong way.
  var pole = b.clone().sub(a);
  pole.sub(axis.clone().multiplyScalar(pole.dot(axis)));
  if (pole.length() < 0.02 || pole.dot(natural) <= 0) pole = natural;
  if (pole.lengthSq() < 1e-10) pole = new T.Vector3(0, 0, limb.bend);
  pole.normalize();
  var cosA = (lenA * lenA + d * d - lenB * lenB) / (2 * lenA * d);
  var angle = Math.acos(Math.max(-1, Math.min(1, cosA)));
  var middle = a.clone().add(axis.clone().multiplyScalar(Math.cos(angle) * lenA))
    .add(pole.clone().multiplyScalar(Math.sin(angle) * lenA));
  _turn(upper, b.clone().sub(a), middle.clone().sub(a));
  var b2 = _worldPos(lower), c2 = _worldPos(end);
  var reach = a.clone().add(axis.clone().multiplyScalar(d));
  _turn(lower, c2.clone().sub(b2), reach.clone().sub(b2));
  if (endWorld) _setWorldQuat(end, endWorld);
}

function _pointerRay(e) {
  var rect = _renderer.domElement.getBoundingClientRect();
  var pointer = new T.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1,
    -((e.clientY - rect.top) / rect.height) * 2 + 1);
  var ray = new T.Raycaster();
  ray.setFromCamera(pointer, _camera);
  return ray;
}

function _onHandleDown(e) {
  if (e.button !== 0 || !_handles.length || (_gizmo && _gizmo.axis)) return;
  _placeHandles();
  // The nearest handle within reach on screen, not an exact hit on a small
  // sphere: a dot a few pixels wide is easy to miss by one.
  var rect = _renderer.domElement.getBoundingClientRect();
  var best = null, bestDistance = 10;
  _handles.forEach(function (h) {
    var p = h.mesh.position.clone().project(_camera);
    if (p.z > 1) return;
    var x = rect.left + (p.x + 1) / 2 * rect.width, y = rect.top + (1 - p.y) / 2 * rect.height;
    var distance = Math.hypot(e.clientX - x, e.clientY - y);
    if (distance < bestDistance) { best = h; bestDistance = distance; }
  });
  if (best) {
    _beginHandleDrag(e, best);
    return;
  }
  // ST4. Then a figure's turn knob, then a body part, then a floor ring.
  var knob = _nearestOnScreen(e, _figures.map(function (f, i) { return { at: f.knob.position, index: i }; }), 16);
  if (knob) { _beginFigureDrag(e, knob.index, "turn"); return; }
  _refreshSkin();
  var ray = _pointerRay(e);
  var hit = ray.intersectObjects(_figures.map(function (f) { return f.mesh; }), false)[0];
  if (hit) {
    var index = _figures.findIndex(function (f) { return f.mesh === hit.object; });
    if (index !== _current) _setCurrent(index);
    var part = _mesh.geometry.attributes.partId.getX(hit.face.a);
    _select(part);
    // Grabbing the hips moves the whole figure; any other part swings.
    if (PARTS[part].joint === "root") _beginFigureDrag(e, index, "move");
    else _beginSwing(e, part, hit.point);
    return;
  }
  var floor = ray.ray.intersectPlane(new T.Plane(new T.Vector3(0, 1, 0), -FLOOR_Y), new T.Vector3());
  if (floor) {
    var onRing = _figures.findIndex(function (f) {
      var r = Math.hypot(floor.x - f.ring.position.x, floor.z - f.ring.position.z);
      return r >= RING_INNER - 0.08 && r <= RING_OUTER + 0.06;
    });
    if (onRing >= 0) { _beginFigureDrag(e, onRing, "move"); return; }
  }
}

function _nearestOnScreen(e, points, reach) {
  var rect = _renderer.domElement.getBoundingClientRect(), best = null, bestDistance = reach;
  points.forEach(function (pt) {
    var p = pt.at.clone().project(_camera);
    if (p.z > 1) return;
    var distance = Math.hypot(e.clientX - (rect.left + (p.x + 1) / 2 * rect.width),
      e.clientY - (rect.top + (1 - p.y) / 2 * rect.height));
    if (distance < bestDistance) { best = pt; bestDistance = distance; }
  });
  return best;
}

function _claim(e) {
  e.stopImmediatePropagation();
  e.preventDefault();
  _orbit.enabled = false;
  window.addEventListener("pointermove", _onHandleMove);
  window.addEventListener("pointerup", _onHandleUp);
}

function _beginHandleDrag(e, best) {
  var joint = best.joint;
  var normal = _camera.getWorldDirection(new T.Vector3());
  var plane = new T.Plane().setFromNormalAndCoplanarPoint(normal, best.mesh.position.clone());
  var grabbed = _pointerRay(e).ray.intersectPlane(plane, new T.Vector3()) || best.mesh.position.clone();
  _drag = {
    kind: "handle",
    joint: joint,
    plane: plane,
    grab: grabbed.sub(best.mesh.position),
    rootStart: _boneByName.root.position.clone(),
    handleStart: best.mesh.position.clone(),
    feet: {},
  };
  if (joint === "root") {
    ["l_foot", "r_foot"].forEach(function (f) {
      _drag.feet[f] = { at: _worldPos(_boneByName[f]), turn: _worldQuat(_boneByName[f]) };
    });
  } else {
    _drag.endWorld = _worldQuat(_boneByName[joint]);
  }
  _claim(e);
}

// Slide a whole figure on the floor, or turn it about its hips.
function _beginFigureDrag(e, index, kind) {
  if (index !== _current) { _setCurrent(index); _select(-1); }
  var root = _figures[index].boneByName.root;
  var floor = new T.Plane(new T.Vector3(0, 1, 0), -FLOOR_Y);
  var start = _pointerRay(e).ray.intersectPlane(floor, new T.Vector3());
  if (!start) return;
  _mesh.updateMatrixWorld(true);
  _drag = {
    kind: kind, index: index, plane: floor, start: start,
    rootStart: root.position.clone(), rootTurn: root.quaternion.clone(),
    centre: _worldPos(root),
  };
  _claim(e);
}

// Swing a part about its joint so the grabbed point follows the cursor; with
// Shift, twist it about its own length instead.
var TIPS = {
  c_spine1: "c_spine3", c_spine3: "c_neck", c_neck: "c_head", c_head: "c_head_null",
  l_uparm: "l_lowarm", l_lowarm: "l_wrist", l_wrist: "l_middle1",
  r_uparm: "r_lowarm", r_lowarm: "r_wrist", r_wrist: "r_middle1",
  l_upleg: "l_lowleg", l_lowleg: "l_foot", l_foot: "l_ball",
  r_upleg: "r_lowleg", r_lowleg: "r_foot", r_foot: "r_ball",
};

function _beginSwing(e, part, grabbedPoint) {
  var bone = _boneByName[PARTS[part].joint];
  _mesh.updateMatrixWorld(true);
  var pivot = _worldPos(bone);
  var tip = TIPS[bone.name] && _boneByName[TIPS[bone.name]] ? _worldPos(_boneByName[TIPS[bone.name]]) : grabbedPoint;
  var from = grabbedPoint.clone().sub(pivot);
  if (from.length() < 0.02) from = tip.clone().sub(pivot);
  var normal = _camera.getWorldDirection(new T.Vector3());
  _drag = {
    kind: "swing", bone: bone, pivot: pivot, from: from,
    axis: tip.clone().sub(pivot).normalize(), startWorld: _worldQuat(bone),
    plane: new T.Plane().setFromNormalAndCoplanarPoint(normal, grabbedPoint.clone()),
    x0: e.clientX,
  };
  _claim(e);
}

function _onHandleMove(e) {
  if (!_drag) return;
  var point = _pointerRay(e).ray.intersectPlane(_drag.plane, new T.Vector3());
  if (!point) return;
  if (_drag.kind === "swing") {
    var q;
    if (e.shiftKey) {
      q = new T.Quaternion().setFromAxisAngle(_drag.axis, (e.clientX - _drag.x0) * 0.012);
    } else {
      q = new T.Quaternion().setFromUnitVectors(_drag.from.clone().normalize(),
        point.clone().sub(_drag.pivot).normalize());
    }
    _setWorldQuat(_drag.bone, q.multiply(_drag.startWorld.clone()));
  } else if (_drag.kind === "move") {
    // The root's parent sits at the origin unrotated: a world offset is local.
    var root = _figures[_drag.index].boneByName.root;
    root.position.set(_drag.rootStart.x + point.x - _drag.start.x, _drag.rootStart.y,
                      _drag.rootStart.z + point.z - _drag.start.z);
    root.updateMatrixWorld(true);
  } else if (_drag.kind === "turn") {
    var c = _drag.centre;
    var a0 = Math.atan2(_drag.start.x - c.x, _drag.start.z - c.z);
    var a1 = Math.atan2(point.x - c.x, point.z - c.z);
    var yaw = new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 1, 0), a1 - a0);
    var turnRoot = _figures[_drag.index].boneByName.root;
    turnRoot.quaternion.copy(yaw.multiply(_drag.rootTurn.clone()));
    turnRoot.updateMatrixWorld(true);
  } else {
    point.sub(_drag.grab);
    if (_drag.joint === "root") {
      var hips = _boneByName.root;
      hips.position.copy(_drag.rootStart).add(point.clone().sub(_drag.handleStart));
      hips.updateMatrixWorld(true);
      Object.keys(_drag.feet).forEach(function (f) { _solveLimb(f, _drag.feet[f].at, _drag.feet[f].turn); });
    } else {
      _solveLimb(_drag.joint, point, _drag.endWorld);
    }
  }
  _changed(true);
}

function _onHandleUp() {
  _drag = null;
  _orbit.enabled = true;
  window.removeEventListener("pointermove", _onHandleMove);
  window.removeEventListener("pointerup", _onHandleUp);
}

// ── posing ───────────────────────────────────────────────────────────────
// A whole pose: every joint's rotation, and where the hips are. Without a
// position the figure stays where it stands and keeps facing the way it
// faces (a preset or Reset changes the pose, not the staging); the hips go to
// their resting height for this figure's body.
function _setLocal(rotations, rootPosition) {
  var figure = _figures[_current], root = _boneByName.root;
  var turn = root.quaternion.clone(), x = root.position.x, z = root.position.z;
  _bones.forEach(function (bone, index) {
    var q = rotations && rotations[bone.name];
    if (q) bone.quaternion.fromArray(q);
    else bone.quaternion.copy(_restLocal[index]);
  });
  if (!(rotations && rotations.root)) root.quaternion.copy(turn);
  if (rootPosition) {
    root.position.fromArray(rootPosition);
  } else if (figure) {
    var body = _bodies[figure.body] || _bodies.adult;
    root.position.set(x, (body.rootRest.y - (body.floor || 0)) * (figure.scale || 1), z);
  }
}

function applyPreset(id) {
  var preset = _meta && _meta.presets[id];
  if (!preset) return false;
  _setLocal(preset.rotations);
  _changed(true);
  return true;
}

function resetPose() { _setLocal(null); _changed(true); }

// ST7. A pose read from a picture (`/studio/stage/pose_from_picture`): every
// MHR joint's rotation in the picture's camera space (x right, y up, z toward
// the camera), in bone order. Each bone takes its rotation relative to its
// parent. The figure stays upright where it stands and turns to face Stage's
// camera the way the person faced the picture's; the tilt of the picture's own
// camera is not the figure's. Then it is set down so its lowest joint is where
// a standing figure's is, so a kneeling or sitting pose does not float.
function _yawOnly(q) {
  var t = new T.Quaternion(0, q.y, 0, q.w);
  return t.lengthSq() < 1e-12 ? new T.Quaternion() : t.normalize();
}

function applyReadPose(world) {
  var figure = _figures[_current];
  if (!figure || !_camera || !Array.isArray(world) || world.length !== _bones.length) return false;
  var read = world.map(function (q) { return new T.Quaternion().fromArray(q); });
  var rotations = {};
  _bones.forEach(function (bone, i) {
    var p = _bones.indexOf(bone.parent);
    if (p >= 0) rotations[bone.name] = read[p].clone().invert().multiply(read[i]).toArray();
  });
  var root = _boneByName.root;
  var facing = _yawOnly(new T.Quaternion().fromArray(rotations.root || [0, 0, 0, 1]));
  rotations.root = _yawOnly(_camera.getWorldQuaternion(new T.Quaternion())).multiply(facing).toArray();
  _setLocal(rotations);
  var body = _bodies[figure.body] || _bodies.adult;
  _mesh.updateMatrixWorld(true);
  var lowest = Infinity, at = new T.Vector3();
  _bones.forEach(function (b) {
    if (!(b.parent && b.parent.isBone)) return;   // body_world: the origin, not a body part
    b.getWorldPosition(at);
    if (at.y < lowest) lowest = at.y;
  });
  if (isFinite(lowest)) root.position.y += (body.lowest - (body.floor || 0)) * (figure.scale || 1) - lowest;
  _changed(true);
  return true;
}

// ST7. "Pose from a picture" is opt-in: its model is a 2.8 GB download under
// Meta's SAM License, fetched only when the owner presses Get. The picture is
// scaled down here (the model reads a 512 px crop of the person) and sent to
// this computer's Studio only.
var _pictureModel = { installed: false, state: "idle" };
var PICTURE_MAX_SIDE = 1536;

function _pictureNote(text) { if (_els.pictureNote) _els.pictureNote.textContent = text || ""; }

function _serverMessage(error) {
  var text = String((error && error.message) || error || "");
  var cut = text.indexOf(" — ");
  return cut >= 0 ? text.slice(cut + 3) : text;
}

function _showPictureModel() {
  var s = _pictureModel || {};
  if (!_els.pictureBtn) return;
  _els.pictureBtn.disabled = !s.installed || !_mesh;
  _els.pictureGet.hidden = !!s.installed || s.state === "downloading";
  if (s.installed) {
    if (s.state === "done") _pictureNote(_t("stage.picture.ready", "The pose-reading model is ready. Choose a picture."));
    else _pictureNote(_t("stage.picture.hint", "Reads one person's pose from a picture you choose. The picture stays on this computer."));
  } else if (s.state === "downloading") {
    _pictureNote(_t("stage.picture.downloading", "Downloading the pose-reading model: {percent}%",
      { percent: Math.floor(100 * (s.received || 0) / (s.total || 1)) }));
  } else if (s.state === "failed") {
    _pictureNote(s.error || _t("stage.picture.failed", "The download did not finish."));
  } else {
    _pictureNote(_t("stage.picture.offer",
      "Needs SAM 3D Body (Meta), a 2.8 GB download under Meta's SAM License (see Licensing in the Codex). Nothing is downloaded until you press Get."));
  }
}

function _refreshPictureModel() {
  if (!window.API) return Promise.resolve(_pictureModel);
  return window.API.get("/studio/stage/picture_model").then(function (status) {
    _pictureModel = status || _pictureModel;
    _showPictureModel();
    if (_pictureModel.state === "downloading") setTimeout(_refreshPictureModel, 1000);
    return _pictureModel;
  }, function () { return _pictureModel; });
}

function _getPictureModel() {
  if (!window.API) return;
  _els.pictureGet.hidden = true;
  window.API.post("/studio/stage/picture_model", {}).then(function (status) {
    _pictureModel = status || _pictureModel;
    _showPictureModel();
    if (_pictureModel.state === "downloading") setTimeout(_refreshPictureModel, 1000);
  }, function (error) {
    _els.pictureGet.hidden = false;
    _pictureNote(_serverMessage(error));
  });
}

function _readPicture(file) {
  if (!window.API || !file) return;
  _els.pictureBtn.disabled = true;
  _pictureNote(_t("stage.picture.reading", "Reading the pose. The first time also loads the model, about 10 seconds."));
  var done = function (text) { _pictureNote(text); _showPictureButton(); };
  var reader = new FileReader();
  reader.onerror = function () { done(_t("stage.picture.notPicture", "That file is not a picture Studio can read.")); };
  reader.onload = function () {
    var img = new Image();
    img.onerror = reader.onerror;
    img.onload = function () {
      var scale = Math.min(1, PICTURE_MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
      var canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      window.API.post("/studio/stage/pose_from_picture", { image: canvas.toDataURL("image/jpeg", 0.92) })
        .then(function (reply) {
          var person = reply && reply.people && reply.people[0];
          if (!person || !applyReadPose(person.world)) {
            done(_t("stage.picture.noPerson", "No person was found in that picture."));
            return;
          }
          done(_t("stage.picture.done", "Pose read. Adjust it like any other pose."));
        }, function (error) { done(_serverMessage(error)); });
    };
    img.src = reader.result;
  };
  reader.readAsDataURL(file);
}

function _showPictureButton() {
  if (_els.pictureBtn) _els.pictureBtn.disabled = !(_pictureModel && _pictureModel.installed) || !_mesh;
}

function resetPart() {
  if (_selected < 0) return;
  var bone = _boneByName[PARTS[_selected].joint];
  bone.quaternion.copy(_restLocal[_bones.indexOf(bone)]);
  _changed(true);
}

// Mirror left and right through the figure's middle. Worked in world space,
// so it holds whatever rest frames MHR gives each side:
//   new_world[j] = M(world[partner]) * M(rest[partner])^-1 * rest[j],
// with M the reflection x -> -x, which maps (x, y, z, w) to (x, -y, -z, w).
function mirrorPose() {
  _mesh.updateMatrixWorld(true);
  var world = _bones.map(function (b) { return b.getWorldQuaternion(new T.Quaternion()); });
  var reflect = function (q) { return new T.Quaternion(q.x, -q.y, -q.z, q.w); };
  var index = {};
  _bones.forEach(function (b, i) { index[b.name] = i; });
  var partner = function (name) {
    if (name.indexOf("l_") === 0) return "r_" + name.slice(2);
    if (name.indexOf("r_") === 0) return "l_" + name.slice(2);
    return name;
  };
  var next = _bones.map(function (b, i) {
    var p = index[partner(b.name)];
    if (p === undefined) p = i;
    return reflect(world[p]).multiply(reflect(_restWorld[p]).invert()).multiply(_restWorld[i]);
  });
  var meshWorld = _mesh.getWorldQuaternion(new T.Quaternion());
  _bones.forEach(function (b, i) {
    var parentWorld = b.parent && b.parent.isBone ? next[_bones.indexOf(b.parent)] : meshWorld;
    b.quaternion.copy(parentWorld.clone().invert().multiply(next[i]));
  });
  _changed(true);
}

// ST4. The rotate rings are an option for fine turning, off by default
// (owner, 2026-11-07: "the rings still suck"); dragging a part swings it.
var _rings = false;

function setRings(on) {
  _rings = !!on;
  if (_els.ringsBox) _els.ringsBox.checked = _rings;
  _select(_selected);
}

function _select(part) {
  _selected = part;
  if (_material) _material.uniforms.highlightPart.value = part;
  if (_gizmo) {
    if (part >= 0 && _rings) _gizmo.attach(_boneByName[PARTS[part].joint]);
    else _gizmo.detach();
  }
  if (_els.part) {
    _els.part.textContent = part >= 0 ? _t(PARTS[part].key, PARTS[part].label)
      : _t("stage.partNone", "Drag a body part to pose it.");
  }
  if (_els.resetPart) _els.resetPart.disabled = part < 0;
  _draw();
}

var _down = null;
function _onPointerDown(e) { _down = { x: e.clientX, y: e.clientY }; }
function _onPointerUp(e) {
  if (!_down || e.button !== 0) return;
  var moved = Math.abs(e.clientX - _down.x) + Math.abs(e.clientY - _down.y);
  _down = null;
  if (moved > 4 || (_gizmo && _gizmo.dragging)) return;
  var rect = e.target.getBoundingClientRect();
  var pointer = new T.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1,
    -((e.clientY - rect.top) / rect.height) * 2 + 1);
  _refreshSkin();
  var ray = new T.Raycaster();
  ray.setFromCamera(pointer, _camera);
  // The nearest figure under the pointer becomes the current one.
  var hit = ray.intersectObjects(_figures.map(function (f) { return f.mesh; }), false)[0];
  if (!hit) { if (!(_gizmo && _gizmo.axis)) _select(-1); return; }
  var index = _figures.findIndex(function (f) { return f.mesh === hit.object; });
  if (index !== _current) _setCurrent(index);
  _select(_mesh.geometry.attributes.partId.getX(hit.face.a));
}

// ── camera and frame ─────────────────────────────────────────────────────
// The map's camera is the scene camera with the lens's own field of view at
// the picture's shape. The viewport widens its view around it so the frame
// (the map's exact edges) sits inside the window with a margin; the map
// therefore never depends on the window's size.
function _frameRect() {
  var vw = _viewport ? _viewport.clientWidth : 1200, vh = _viewport ? _viewport.clientHeight : 800;
  var pic = _picture(), aspect = pic.width / pic.height;
  var scale = Math.min(0.9, 0.9 * (vw / vh) / aspect);
  var fh = vh * scale, fw = fh * aspect;
  return { vw: vw, vh: vh, scale: scale, x: (vw - fw) / 2, y: (vh - fh) / 2, w: fw, h: fh };
}

function _mapCamera(width, height) {
  var cam = new T.PerspectiveCamera(_fovFromFocal(_focal), width / height, 0.05, 100);
  cam.position.copy(_camera.position);
  cam.quaternion.copy(_camera.quaternion);
  cam.updateMatrixWorld(true);
  cam.updateProjectionMatrix();
  return cam;
}

// three.js keeps a skinned mesh's joint matrices and bounds from the last draw;
// clicking and framing need the current pose.
function _refreshSkin() {
  _figures.forEach(function (f) {
    f.mesh.updateMatrixWorld(true);
    f.mesh.skeleton.update();
    f.mesh.computeBoundingBox();
    f.mesh.computeBoundingSphere();
  });
}

// Frames every figure, not only the current one.
function _frameFigure() {
  _refreshSkin();
  var box = new T.Box3();
  _figures.forEach(function (f) { box.union(new T.Box3().setFromObject(f.mesh, true)); });
  var centre = box.getCenter(new T.Vector3());
  var size = box.getSize(new T.Vector3());
  var pic = _picture();
  var fov = _fovFromFocal(_focal) * Math.PI / 180;
  var hfov = 2 * Math.atan(Math.tan(fov / 2) * pic.width / pic.height);
  var radius = 0.5 * Math.max(size.y, Math.max(size.x, size.z) * 0.6);
  var distance = Math.max(radius / Math.tan(fov / 2), (0.5 * Math.max(size.x, size.z)) / Math.tan(hfov / 2)) * 1.12
    + 0.5 * size.z;
  var dir = _camera.position.clone().sub(_orbit.target);
  if (dir.lengthSq() < 1e-6) dir.set(0, 0, 1);
  dir.normalize();
  _orbit.target.copy(centre);
  _camera.position.copy(centre).addScaledVector(dir, distance);
  _orbit.update();
  _changed(true);
}

function setFocal(mm) {
  _focal = Math.max(14, Math.min(200, mm));
  if (_els.lensValue) _els.lensValue.textContent = Math.round(_focal) + " mm";
  _changed(true);
}

function _layout() {
  if (!_renderer || !_viewport) return;
  var r = _frameRect();
  _renderer.setSize(r.vw, r.vh, false);
  _renderer.domElement.style.width = r.vw + "px";
  _renderer.domElement.style.height = r.vh + "px";
  // Widen the viewport's field so the map's frustum fills the frame.
  var tanHalf = Math.tan(_fovFromFocal(_focal) * Math.PI / 360) / r.scale;
  _camera.fov = 2 * Math.atan(tanHalf) * 180 / Math.PI;
  _camera.aspect = r.vw / r.vh;
  _camera.updateProjectionMatrix();
  if (_frameEl) {
    _frameEl.style.left = r.x + "px"; _frameEl.style.top = r.y + "px";
    _frameEl.style.width = r.w + "px"; _frameEl.style.height = r.h + "px";
  }
  if (_els.size) {
    var pic = _picture();
    _els.size.textContent = _t("stage.frameSize", "Picture {w} × {h}", { w: pic.width, h: pic.height });
  }
}

function _draw() {
  if (!_renderer || !_active) return;
  if (_raf) return;
  _raf = requestAnimationFrame(function () {
    _raf = 0;
    _layout();
    if (_handles.length) _placeHandles();
    _renderer.setRenderTarget(null);
    _renderer.setClearColor(0x16161a, 1);
    _renderer.render(_scene, _camera);
  });
}

// ── the map ──────────────────────────────────────────────────────────────
// Synchronous once Stage is ready: the ControlNet panel asks while it builds
// a Generate request. Returns a 2D canvas at width x height, or null.
// `mode`: "mesh" (the shaded figures, for Anima's 3D-pose adapter), "pose"
// (OpenPose sticks) or "depth" (inverse depth) for SD 1.5 and SDXL (ST5).
var MAP_MODES = ["mesh", "pose", "depth"];
function renderMap(width, height, mode) {
  if (!_renderer || !_mesh) return null;
  width = Math.max(64, Math.round(width)); height = Math.max(64, Math.round(height));
  if (mode === "pose") return _renderPose(width, height);
  if (mode === "depth") return _renderDepth(width, height);
  return _renderMesh(width, height);
}

// The shaded figures, the training recipe.
function _renderMesh(width, height) {
  if (!_renderer || !_mesh) return null;
  width = Math.max(64, Math.round(width)); height = Math.max(64, Math.round(height));
  var key = width + "x" + height;
  var target = _targets[key];
  if (!target) {
    target = new T.WebGLRenderTarget(width, height, { samples: 4 });
    _targets[key] = target;
  }
  var cam = _mapCamera(width, height);
  var helper = _gizmo ? _gizmo.getHelper() : null;
  var hidden = [_grid, helper].concat(_handles.map(function (h) { return h.mesh; }))
    .concat(_figures.map(function (f) { return f.ring; }), _figures.map(function (f) { return f.knob; }))
    .filter(Boolean).map(function (o) { var v = o.visible; o.visible = false; return [o, v]; });
  var highlight = _material.uniforms.highlightPart.value;
  _figures.forEach(function (f) { f.material.uniforms.highlightPart.value = -1; });
  var prevTarget = _renderer.getRenderTarget();
  _renderer.setRenderTarget(target);
  _renderer.setClearColor(0x000000, 1);
  _renderer.clear();
  _renderer.render(_scene, cam);
  var pixels = new Uint8Array(width * height * 4);
  _renderer.readRenderTargetPixels(target, 0, 0, width, height, pixels);
  _renderer.setRenderTarget(prevTarget);
  _material.uniforms.highlightPart.value = highlight;
  hidden.forEach(function (pair) { pair[0].visible = pair[1]; });

  var out = document.createElement("canvas");
  out.width = width; out.height = height;
  var ctx = out.getContext("2d");
  var image = ctx.createImageData(width, height);
  var row = width * 4;
  for (var y = 0; y < height; y++) {   // WebGL rows run bottom-up
    image.data.set(pixels.subarray((height - 1 - y) * row, (height - y) * row), y * row);
  }
  for (var i = 3; i < image.data.length; i += 4) image.data[i] = 255;
  ctx.putImageData(image, 0, 0);
  return out;
}

// ── maps for other model families ────────────────────────────────────────
// ST5. SDXL and SD 1.5 ControlNets read depth or OpenPose sticks, not the
// shaded figure. Both come exactly from the scene.
//
// Depth: relative INVERSE depth, white near and black far, stretched to the
// figures' own nearest and farthest points -- the form Depth Anything and
// MiDaS output and depth ControlNets were trained on. Background black.
var DEPTH_FRAGMENT = [
  "uniform float invNear;",
  "uniform float invFar;",
  "varying vec3 vNormalView;",
  "varying vec3 vViewPos;",
  "varying float vPart;",
  "void main() {",
  "  float d = 1.0 / max(vViewPos.z, 1e-4);",
  "  float v = clamp((d - invFar) / max(invNear - invFar, 1e-6), 0.0, 1.0);",
  "  gl_FragColor = vec4(vec3(v), 1.0);",
  "}",
].join("\n");
var _depthMaterial = null;

function _renderDepth(width, height) {
  if (!_depthMaterial) {
    _depthMaterial = new T.ShaderMaterial({
      vertexShader: VERTEX, fragmentShader: DEPTH_FRAGMENT, side: T.DoubleSide,
      uniforms: { invNear: { value: 1 }, invFar: { value: 0.1 } },
    });
  }
  // Near and far from the figures' skinned bounds, in the map camera's view.
  _refreshSkin();
  var cam = _mapCamera(width, height), view = cam.matrixWorldInverse;
  var near = Infinity, far = 0;
  _figures.forEach(function (f) {
    var box = new T.Box3().setFromObject(f.mesh, true);
    [box.min.x, box.max.x].forEach(function (x) { [box.min.y, box.max.y].forEach(function (y) {
      [box.min.z, box.max.z].forEach(function (z) {
        var d = -new T.Vector3(x, y, z).applyMatrix4(view).z;
        if (d > 0.01) { near = Math.min(near, d); far = Math.max(far, d); }
      });
    }); });
  });
  if (!isFinite(near) || far <= near) { near = 0.5; far = 10; }
  _depthMaterial.uniforms.invNear.value = 1 / near;
  _depthMaterial.uniforms.invFar.value = 1 / far;
  var saved = _figures.map(function (f) { return f.mesh.material; });
  _figures.forEach(function (f) { f.mesh.material = _depthMaterial; });
  var map = _renderMesh(width, height);
  _figures.forEach(function (f, i) { f.mesh.material = saved[i]; });
  // The bounds reach nearer and farther than the skin, so the values do not
  // span the range. Stretch them (still linear in inverse depth): nearest
  // skin white, farthest skin well clear of the black background.
  if (map) {
    var ctx = map.getContext("2d"), img = ctx.getImageData(0, 0, width, height), d = img.data;
    var lo = 255, hi = 0, i;
    for (i = 0; i < d.length; i += 4) if (d[i] > 0) { if (d[i] < lo) lo = d[i]; if (d[i] > hi) hi = d[i]; }
    if (hi > lo) {
      for (i = 0; i < d.length; i += 4) {
        if (!d[i]) continue;
        var v = Math.round(DEPTH_FLOOR + (255 - DEPTH_FLOOR) * (d[i] - lo) / (hi - lo));
        d[i] = d[i + 1] = d[i + 2] = v;
      }
      ctx.putImageData(img, 0, 0);
    }
  }
  return map;
}
var DEPTH_FLOOR = 32;

// OpenPose: Neo's own drawing (`annotator/openpose/util.py` draw_bodypose,
// draw_handpose), drawn at the map's size with Neo's widths scaled from the
// 512 px it draws at. COCO-18 order; MHR's "r_" is the figure's right.
var POSE_LIMBS = [[2, 3], [2, 6], [3, 4], [4, 5], [6, 7], [7, 8], [2, 9], [9, 10], [10, 11], [2, 12],
                  [12, 13], [13, 14], [2, 1], [1, 15], [15, 17], [1, 16], [16, 18]];
var POSE_COLORS = [[255, 0, 0], [255, 85, 0], [255, 170, 0], [255, 255, 0], [170, 255, 0], [85, 255, 0],
                   [0, 255, 0], [0, 255, 85], [0, 255, 170], [0, 255, 255], [0, 170, 255], [0, 85, 255],
                   [0, 0, 255], [85, 0, 255], [170, 0, 255], [255, 0, 255], [255, 0, 170], [255, 0, 85]];
var HAND_FINGERS = ["thumb", "index", "middle", "ring", "pinky"];
var HAND_EDGES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [0, 9], [9, 10], [10, 11],
                  [11, 12], [0, 13], [13, 14], [14, 15], [15, 16], [0, 17], [17, 18], [18, 19], [19, 20]];
var _faceVertices = null;   // { nose, rEar, lEar } vertex indices, found once

function _findFaceVertices() {
  var pos = _geometry.attributes.position, part = _geometry.attributes.partId;
  var headPart = PARTS.findIndex(function (p) { return p.joint === "c_head"; });
  var names = _meta.joints.map(function (j) { return j.name; });
  var eyeY = (_meta.joints[names.indexOf("r_eye")].position[1] + _meta.joints[names.indexOf("l_eye")].position[1]) / 2;
  var nose = -1, rEar = -1, lEar = -1;
  for (var i = 0; i < pos.count; i++) {
    if (part.getX(i) !== headPart) continue;
    var x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
    if (Math.abs(y - (eyeY - 0.035)) < 0.02 && (nose < 0 || z > pos.getZ(nose))) nose = i;
    if (Math.abs(y - eyeY) < 0.025) {
      if (lEar < 0 || x > pos.getX(lEar)) lEar = i;
      if (rEar < 0 || x < pos.getX(rEar)) rEar = i;
    }
  }
  _faceVertices = { nose: nose, rEar: rEar, lEar: lEar };
}

function _figureKeypoints(figure, cam, width, height) {
  if (!_faceVertices) _findFaceVertices();
  figure.mesh.updateMatrixWorld(true);
  figure.mesh.skeleton.update();
  var toPx = function (world) {
    var p = world.clone().project(cam);
    if (p.z > 1 || p.z < -1) return null;
    return [(p.x + 1) / 2 * width, (1 - p.y) / 2 * height];
  };
  var joint = function (name) { return toPx(_worldPos(figure.boneByName[name])); };
  var vertex = function (index) {
    if (index < 0) return null;
    return toPx(figure.mesh.getVertexPosition(index, new T.Vector3()).applyMatrix4(figure.mesh.matrixWorld));
  };
  var rSh = _worldPos(figure.boneByName.r_uparm), lSh = _worldPos(figure.boneByName.l_uparm);
  var body = [
    vertex(_faceVertices.nose), toPx(rSh.clone().add(lSh).multiplyScalar(0.5)),
    joint("r_uparm"), joint("r_lowarm"), joint("r_wrist"),
    joint("l_uparm"), joint("l_lowarm"), joint("l_wrist"),
    joint("r_upleg"), joint("r_lowleg"), joint("r_foot"),
    joint("l_upleg"), joint("l_lowleg"), joint("l_foot"),
    joint("r_eye"), joint("l_eye"), vertex(_faceVertices.rEar), vertex(_faceVertices.lEar),
  ];
  var hand = function (side) {
    var points = [joint(side + "_wrist")];
    HAND_FINGERS.forEach(function (finger) {
      [finger + "1", finger + "2", finger + "3", finger + "_null"].forEach(function (name) {
        points.push(figure.boneByName[side + "_" + name] ? joint(side + "_" + name) : null);
      });
    });
    return points;
  };
  return { body: body, hands: [hand("l"), hand("r")] };
}

function _renderPose(width, height) {
  var cam = _mapCamera(width, height);
  var out = document.createElement("canvas");
  out.width = width; out.height = height;
  var ctx = out.getContext("2d");
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, width, height);
  var s = Math.min(width, height) / 512;
  _figures.forEach(function (figure) {
    var k = _figureKeypoints(figure, cam, width, height);
    POSE_LIMBS.forEach(function (limb, i) {
      var a = k.body[limb[0] - 1], b = k.body[limb[1] - 1];
      if (!a || !b) return;
      var c = POSE_COLORS[i];
      ctx.fillStyle = "rgb(" + Math.floor(c[0] * 0.6) + "," + Math.floor(c[1] * 0.6) + "," + Math.floor(c[2] * 0.6) + ")";
      ctx.beginPath();
      ctx.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.hypot(a[0] - b[0], a[1] - b[1]) / 2, 4 * s,
                  Math.atan2(a[1] - b[1], a[0] - b[0]), 0, Math.PI * 2);
      ctx.fill();
    });
    k.body.forEach(function (p, i) {
      if (!p) return;
      var c = POSE_COLORS[i];
      ctx.fillStyle = "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")";
      ctx.beginPath(); ctx.arc(p[0], p[1], 4 * s, 0, Math.PI * 2); ctx.fill();
    });
    k.hands.forEach(function (points) {
      ctx.lineWidth = 2 * s;
      HAND_EDGES.forEach(function (edge, i) {
        var a = points[edge[0]], b = points[edge[1]];
        if (!a || !b) return;
        ctx.strokeStyle = "hsl(" + Math.round(i / HAND_EDGES.length * 360) + ",100%,50%)";
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      });
      ctx.fillStyle = "rgb(0,0,255)";
      points.forEach(function (p) { if (p) { ctx.beginPath(); ctx.arc(p[0], p[1], 4 * s, 0, Math.PI * 2); ctx.fill(); } });
    });
  });
  return out;
}

// ── silhouettes: regions and masks ───────────────────────────────────────
// ST2. Stage knows exactly where each figure is, so a region or mask needs no
// painting and no detection. One figure is drawn white, every other figure
// black, from the map's own camera: figures in front still cover the one
// behind, so its mask has the right hole in it. Parts by the per-vertex part
// ids the figure already carries.
var MASK_PARTS = {
  body: null,                                       // every part
  head: ["stage.part.head"],
  hands: ["stage.part.lHand", "stage.part.rHand"],
};
var MASK_FRAGMENT = [
  "uniform float figureOn;",
  "uniform float parts[" + PARTS.length + "];",
  "varying vec3 vNormalView;",
  "varying vec3 vViewPos;",
  "varying float vPart;",
  "void main() {",
  "  float on = figureOn > 0.5 ? parts[int(vPart + 0.5)] : 0.0;",
  "  gl_FragColor = vec4(vec3(on), 1.0);",
  "}",
].join("\n");
var _maskOn = null, _maskOff = null;

function _maskMaterial(on) {
  return new T.ShaderMaterial({
    vertexShader: VERTEX, fragmentShader: MASK_FRAGMENT, side: T.DoubleSide,
    uniforms: { figureOn: { value: on ? 1 : 0 }, parts: { value: PARTS.map(function () { return 1; }) } },
  });
}

// 0..255 coverage of one figure (or some of its parts), top row first, at
// width x height, through the same camera as the map.
function renderCoverage(figureIndex, kind, width, height) {
  if (!_renderer || !_figures[figureIndex]) return null;
  if (!_maskOn) { _maskOn = _maskMaterial(true); _maskOff = _maskMaterial(false); }
  var keys = MASK_PARTS[kind];
  _maskOn.uniforms.parts.value = PARTS.map(function (p) { return !keys || keys.indexOf(p.key) >= 0 ? 1 : 0; });
  var saved = _figures.map(function (f) { return f.mesh.material; });
  _figures.forEach(function (f, i) { f.mesh.material = i === figureIndex ? _maskOn : _maskOff; });
  var map = _renderMesh(width, height);
  _figures.forEach(function (f, i) { f.mesh.material = saved[i]; });
  if (!map) return null;
  var data = map.getContext("2d").getImageData(0, 0, map.width, map.height).data;
  var out = new Uint8Array(map.width * map.height);
  for (var i = 0; i < out.length; i++) out[i] = data[i * 4];
  return out;
}

// Coverage at the picture's shape, stretched to the Canvas document's size --
// which is how a painted region reaches the picture, so both line up.
function _coverageForDocument(figureIndex, kind, docW, docH) {
  var pic = _picture();
  var cov = renderCoverage(figureIndex, kind, pic.width, pic.height);
  if (!cov) return null;
  var src = document.createElement("canvas");
  src.width = pic.width; src.height = pic.height;
  var img = src.getContext("2d").createImageData(pic.width, pic.height);
  for (var i = 0; i < cov.length; i++) { img.data[i * 4 + 3] = cov[i]; }
  src.getContext("2d").putImageData(img, 0, 0);
  var dst = document.createElement("canvas");
  dst.width = docW; dst.height = docH;
  var ctx = dst.getContext("2d");
  ctx.drawImage(src, 0, 0, docW, docH);
  var data = ctx.getImageData(0, 0, docW, docH).data;
  var out = new Uint8Array(docW * docH);
  for (var j = 0; j < out.length; j++) out[j] = data[j * 4 + 3];
  return out;
}

function _rgb(color) {
  var m = /^#([0-9a-f]{6})$/i.exec(String(color || "").trim());
  if (m) { var n = parseInt(m[1], 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  var r = /rgba?\(\s*(\d+)\D+(\d+)\D+(\d+)/i.exec(String(color || ""));
  return r ? [+r[1], +r[2], +r[3]] : [212, 83, 126];
}

// ST2b. How far a figure's region reaches past its body, as a share of the
// picture's shorter side, so hair and clothes that stick out of the mannequin
// still take the figure's prompt (owner, 2026-11-07). The masks stay exact.
var _regionMargin = 0.03;

// Grow 0..255 coverage by `r` pixels: everything within r of a covered pixel
// (over half on) becomes fully covered. Two sweeps per axis, linear time.
function _dilate(cov, W, H, r) {
  if (r <= 0) return cov;
  var horiz = new Uint8Array(cov.length), out = new Uint8Array(cov.length);
  var x, y, i, near;
  for (y = 0; y < H; y++) {
    near = -1e9;
    for (x = 0; x < W; x++) { i = y * W + x; if (cov[i] > 127) near = x; if (x - near <= r) horiz[i] = 1; }
    near = 1e9;
    for (x = W - 1; x >= 0; x--) { i = y * W + x; if (cov[i] > 127) near = x; if (near - x <= r) horiz[i] = 1; }
  }
  for (x = 0; x < W; x++) {
    near = -1e9;
    for (y = 0; y < H; y++) { i = y * W + x; if (horiz[i]) near = y; if (y - near <= r) out[i] = 255; }
    near = 1e9;
    for (y = H - 1; y >= 0; y--) { i = y * W + x; if (horiz[i]) near = y; if (near - y <= r) out[i] = 255; }
  }
  for (i = 0; i < out.length; i++) if (cov[i] > out[i]) out[i] = cov[i];
  return out;
}

function _regionCoverage(index, W, H) {
  var cov = _coverageForDocument(index, "body", W, H);
  return cov ? _dilate(cov, W, H, Math.round(_regionMargin * Math.min(W, H))) : null;
}

// Draw Stage's part of a linked region and KEEP the owner's own edits: what
// they painted outside Stage's last drawing stays, what they erased inside it
// stays erased. Only Stage's own part moves with the figure. The last drawing
// lives on the region in this session only; without it nothing is erased.
function _paintRegion(region, coverage, W, H) {
  var last = region._stageLast && region._stageLast.length === coverage.length ? region._stageLast : null;
  var current = region.ctx.getImageData(0, 0, W, H).data;
  var img = region.ctx.createImageData(W, H), rgb = _rgb(region.color);
  for (var i = 0; i < coverage.length; i++) {
    var had = current[i * 4 + 3], drawn = last ? last[i] : 0, value = coverage[i];
    if (had > 127 && drawn <= 127) value = Math.max(value, had);   // painted by the owner
    if (last && had <= 127 && drawn > 127) value = 0;              // erased by the owner
    if (!value) continue;
    img.data[i * 4] = rgb[0]; img.data[i * 4 + 1] = rgb[1]; img.data[i * 4 + 2] = rgb[2];
    img.data[i * 4 + 3] = value;
  }
  region._stageLast = coverage;
  region.ctx.clearRect(0, 0, W, H);
  region.ctx.putImageData(img, 0, 0);
}

function setRegionMargin(share) {
  _regionMargin = Math.max(0, Math.min(0.2, share));
  _save();
  if (refreshLinkedRegions()) _canvasRedraw();
}

// One Regional region per figure, kept LINKED to it (`stageFigure`), and named
// masks per figure (body, head, hands) made from the scene as it is now.
function makeRegionsAndMasks() {
  var C = window.StudioCore, S = C && C.state;
  if (!_mesh || !C || !S) return null;
  var W = S.W, H = S.H, made = { regions: 0, masks: 0 };
  // Regions: reuse a figure's linked region, add one if it has none, drop
  // the ones whose figure is gone.
  (S.regions || []).filter(function (r) { return r.stageFigure != null && r.stageFigure >= _figures.length; })
    .forEach(function (r) { C.deleteRegion(r.id); });
  _figures.forEach(function (figure, index) {
    var coverage = _regionCoverage(index, W, H);
    if (!coverage) return;
    var name = _t("stage.figureName", "Figure {n}", { n: index + 1 });
    var region = (S.regions || []).find(function (r) { return r.stageFigure === index; });
    if (!region) { region = C.addRegion(name); region.stageFigure = index; }
    _paintRegion(region, coverage, W, H);
    made.regions++;
  });
  // Masks: replace this figure's Stage masks, keep everything else.
  (S.masks || []).filter(function (m) { return m.origin && m.origin.kind === "stage"; })
    .map(function (m) { return m.id; }).forEach(function (id) { C.deleteMask(id); });
  _figures.forEach(function (figure, index) {
    Object.keys(MASK_PARTS).forEach(function (kind) {
      var coverage = _coverageForDocument(index, kind, W, H);
      if (!coverage) return;
      var name = kind === "body" ? _t("stage.figureName", "Figure {n}", { n: index + 1 })
        : _t("stage.mask." + kind, "Figure {n} " + kind, { n: index + 1 });
      C.createMask(name, { coverage: coverage, target: false, undoLabel: "Stage masks",
                           origin: { kind: "stage", figure: index, part: kind } });
      made.masks++;
    });
  });
  try {
    window.StudioUI?.renderRegionPanel?.();
    window.StudioUI?.renderMaskPanel?.();
    window.StudioUI?.redraw?.();
  } catch (_) { /* the panels repaint on their next change */ }
  return made;
}

// Just before Generate: a linked region follows its figure, wherever it went.
function refreshLinkedRegions() {
  var C = window.StudioCore, S = C && C.state;
  if (!_mesh || !S || !S.regions) return 0;
  var count = 0;
  S.regions.forEach(function (region) {
    if (region.stageFigure == null || !_figures[region.stageFigure]) return;
    var coverage = _regionCoverage(region.stageFigure, S.W, S.H);
    if (coverage) { _paintRegion(region, coverage, S.W, S.H); count++; }
  });
  return count;
}

// ── drawing guide ────────────────────────────────────────────────────────
// ST3. The posed figures, faint, over the Canvas view: something to draw over,
// like an artist's wooden mannequin. Drawn on the VIEW after every Canvas
// redraw (StudioUI.onAfterRedraw, as Develop's before/after split is), never
// into a layer: a visible layer is part of the picture Generate sends and of a
// saved PSD, and a non-blank canvas turns a fresh picture into an edit. The
// guide is in none of them.
var _guide = { on: false, opacity: 0.4, image: null, stale: true, hooked: false };

function _guideImage() {
  var pic = _picture();
  var map = _renderMesh(pic.width, pic.height);
  if (!map) return null;
  var alpha = new Uint8Array(pic.width * pic.height);
  _figures.forEach(function (f, index) {
    var cov = renderCoverage(index, "body", pic.width, pic.height);
    if (cov) for (var i = 0; i < alpha.length; i++) if (cov[i] > alpha[i]) alpha[i] = cov[i];
  });
  var ctx = map.getContext("2d");
  var img = ctx.getImageData(0, 0, pic.width, pic.height);
  for (var j = 0; j < alpha.length; j++) img.data[j * 4 + 3] = alpha[j];
  ctx.putImageData(img, 0, 0);
  return map;
}

function _drawGuide() {
  if (!_guide.on || !_mesh) return;
  var C = window.StudioCore, S = C && C.state;
  if (!S || !S.ctx || !C.applyDisplayTransform) return;
  if (_guide.stale || !_guide.image) { _guide.image = _guideImage(); _guide.stale = false; }
  if (!_guide.image) return;
  var ctx = S.ctx;
  ctx.save();
  C.applyDisplayTransform(ctx);
  ctx.globalAlpha = _guide.opacity;
  ctx.globalCompositeOperation = "source-over";
  ctx.drawImage(_guide.image, 0, 0, S.W, S.H);
  ctx.restore();
}

// A FULL Canvas repaint: a plain redraw may repaint only what changed, which
// would leave a hidden guide's pixels on screen (seen in the browser).
function _canvasRedraw() {
  try {
    window.StudioCore?.markCompositeDirty?.();
    window.StudioUI?.redraw?.();
  } catch (_) { /* repaints on the next Canvas redraw */ }
}

function setGuide(on, opacity) {
  _guide.on = !!on;
  if (typeof opacity === "number") _guide.opacity = Math.max(0.05, Math.min(1, opacity));
  _guide.stale = true;
  if (!_guide.hooked && window.StudioUI && typeof window.StudioUI.onAfterRedraw === "function") {
    window.StudioUI.onAfterRedraw(_drawGuide);
    _guide.hooked = true;
  }
  if (_els.guideToggle) {
    _els.guideToggle.textContent = _guide.on ? _t("stage.guideHide", "Hide the guide on the Canvas")
      : _t("stage.guideShow", "Show as a guide on the Canvas");
  }
  _canvasRedraw();
  _save();
  return _guide.on;
}

// ── saving the scene ─────────────────────────────────────────────────────
// Per browser, so the scene survives a reload. A convenience only: a missing
// or broken record leaves the standing preset.
function _save() {
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(function () {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        v: 2, focal: _focal, current: _current, guide: { on: _guide.on, opacity: _guide.opacity },
        regionMargin: _regionMargin,
        figures: _figures.map(_figurePose),
        camera: _camera.position.toArray(), target: _orbit.target.toArray(),
      }));
    } catch (_) { /* storage unavailable: the scene lasts this session */ }
  }, 300);
}

// One figure as data: its changed joint rotations and where its hips are.
function _figurePose(figure) {
  var rotations = {};
  figure.bones.forEach(function (b, i) {
    if (Math.abs(Math.abs(b.quaternion.dot(_restLocal[i])) - 1) > 1e-7) rotations[b.name] = b.quaternion.toArray();
  });
  return { rotations: rotations, root: figure.boneByName.root.position.toArray(),
           body: figure.body || "adult", height: figure.height || 1 };
}

function _restore() {
  var saved = null;
  try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (_) { saved = null; }
  // v1 held one figure at the top level; v2 holds a list.
  var figures = saved && saved.v === 2 && Array.isArray(saved.figures) ? saved.figures
    : saved && saved.v === 1 && saved.rotations ? [{ rotations: saved.rotations, root: saved.root }] : null;
  if (figures && figures.length) {
    figures.slice(0, MAX_FIGURES).forEach(function (pose, index) {
      var body = _bodies[pose.body] ? pose.body : "adult";
      if (index > 0) _addFigure(true, body);
      else if (body !== "adult") setBody(0, body);
      var figure = _figures[index];
      if (typeof pose.height === "number") { figure.height = Math.max(0.8, Math.min(1.2, pose.height)); _applyBodyScale(figure, 0); }
      _setCurrent(index);
      _setLocal(pose.rotations || {}, Array.isArray(pose.root) && pose.root.length === 3 ? pose.root : null);
    });
    _setCurrent(Math.min(saved.current || 0, _figures.length - 1));
    if (saved.focal) _focal = saved.focal;
    if (saved.guide && typeof saved.guide.opacity === "number") _guide.opacity = saved.guide.opacity;
    if (typeof saved.regionMargin === "number") {
      _regionMargin = Math.max(0, Math.min(0.2, saved.regionMargin));
      if (_els.marginSlider) {
        _els.marginSlider.value = String(Math.round(_regionMargin * 100));
        _els.marginValue.textContent = _els.marginSlider.value + "%";
      }
    }
    if (saved.guide && saved.guide.on) setTimeout(function () { setGuide(true); }, 0);
    if (saved.camera && saved.target) {
      _camera.position.fromArray(saved.camera);
      _orbit.target.fromArray(saved.target);
      _orbit.update();
    }
  } else {
    applyPreset("standing");
    _frameFigure();
  }
}

var _listeners = [];
function onChange(fn) { _listeners.push(fn); }
function _changed(save) {
  _guide.stale = true;
  if (save) _save();
  _draw();
  _listeners.forEach(function (fn) { try { fn(); } catch (_) { /* a listener's own fault */ } });
}

// ── the tab ──────────────────────────────────────────────────────────────
function _button(label, onClick, cls) {
  var b = document.createElement("button");
  b.type = "button";
  b.className = cls || "stage-btn";
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

function _section(title) {
  var s = document.createElement("section");
  s.className = "stage-section";
  var h = document.createElement("h3");
  h.className = "stage-section-title";
  h.textContent = title;
  s.appendChild(h);
  return s;
}

function _buildUI(container) {
  // The module system styles the container itself (it shows and hides it), so
  // Stage's layout lives on an element of its own inside it.
  container.textContent = "";
  var root = document.createElement("div");
  root.className = "stage-root";
  container.appendChild(root);
  container = root;
  _viewport = document.createElement("div");
  _viewport.className = "stage-viewport";
  _frameEl = document.createElement("div");
  _frameEl.className = "stage-frame";
  _els.size = document.createElement("span");
  _els.size.className = "stage-frame-size";
  _frameEl.appendChild(_els.size);
  _els.status = document.createElement("div");
  _els.status.className = "stage-status";
  _els.status.textContent = _t("stage.loading", "Loading the figure…");
  _viewport.appendChild(_els.status);

  var panel = document.createElement("aside");
  panel.className = "stage-panel";

  var figures = _section(_t("stage.figures", "Figures"));
  _els.figures = document.createElement("div");
  _els.figures.className = "stage-figures";
  figures.appendChild(_els.figures);
  var figureActions = document.createElement("div");
  figureActions.className = "stage-row";
  _els.addFigure = _button(_t("stage.addFigure", "Add figure"), function () { if (_mesh) _addFigure(false); });
  _els.removeFigure = _button(_t("stage.removeFigure", "Remove"), function () { removeFigure(_current); });
  figureActions.appendChild(_els.addFigure);
  figureActions.appendChild(_els.removeFigure);
  figures.appendChild(figureActions);
  // ST6. The current figure's body type and height.
  var bodyRow = document.createElement("label");
  bodyRow.className = "stage-lens";
  var bodyLabel = document.createElement("span");
  bodyLabel.textContent = _t("stage.body", "Body");
  _els.body = document.createElement("select");
  _els.body.className = "stage-select";
  _els.body.id = "stageBody";
  BODY_TYPES.forEach(function (b) {
    var option = document.createElement("option");
    option.value = b.id; option.textContent = _t(b.key, b.label);
    _els.body.appendChild(option);
  });
  _els.body.addEventListener("change", function () { if (_mesh) setBody(_current, _els.body.value); });
  bodyRow.appendChild(bodyLabel);
  bodyRow.appendChild(_els.body);
  figures.appendChild(bodyRow);
  var heightRow = document.createElement("label");
  heightRow.className = "stage-lens";
  var heightLabel = document.createElement("span");
  heightLabel.textContent = _t("stage.height", "Height");
  _els.heightValue = document.createElement("span");
  _els.heightValue.className = "stage-lens-value";
  _els.height = document.createElement("input");
  _els.height.type = "range"; _els.height.min = "80"; _els.height.max = "120"; _els.height.step = "1";
  _els.height.id = "stageHeight"; _els.height.value = "100";
  _els.heightValue.textContent = "100%";
  _els.height.addEventListener("input", function () {
    _els.heightValue.textContent = _els.height.value + "%";
    if (_mesh) setHeight(_current, parseFloat(_els.height.value) / 100);
  });
  heightRow.appendChild(heightLabel); heightRow.appendChild(_els.heightValue); heightRow.appendChild(_els.height);
  figures.appendChild(heightRow);
  panel.appendChild(figures);

  var pose = _section(_t("stage.pose", "Pose"));
  var presets = document.createElement("div");
  presets.className = "stage-grid";
  PRESETS.forEach(function (p) {
    presets.appendChild(_button(_t(p.key, p.label), function () { applyPreset(p.id); }));
  });
  pose.appendChild(presets);
  var poseActions = document.createElement("div");
  poseActions.className = "stage-row";
  poseActions.appendChild(_button(_t("stage.mirror", "Mirror"), mirrorPose));
  poseActions.appendChild(_button(_t("stage.reset", "Reset"), resetPose));
  pose.appendChild(poseActions);
  var handleHint = document.createElement("p");
  handleHint.className = "stage-hint";
  handleHint.textContent = _t("stage.handleHint",
    "Drag a blue dot to move a hand or foot; the limb follows. Drag the pink dot to move the hips while the feet stay put.");
  pose.appendChild(handleHint);
  // ST7. Pose from a picture: the owner chooses the picture; the model is opt-in.
  var picture = document.createElement("div");
  picture.className = "stage-picture";
  _els.pictureFile = document.createElement("input");
  _els.pictureFile.type = "file";
  _els.pictureFile.accept = "image/png,image/jpeg,image/webp,image/gif,image/bmp";
  _els.pictureFile.hidden = true;
  _els.pictureFile.addEventListener("change", function () {
    var file = _els.pictureFile.files && _els.pictureFile.files[0];
    _els.pictureFile.value = "";
    if (file) _readPicture(file);
  });
  var pictureRow = document.createElement("div");
  pictureRow.className = "stage-row";
  _els.pictureBtn = _button(_t("stage.picture.button", "Pose from a picture…"), function () {
    if (_pictureModel && _pictureModel.installed) _els.pictureFile.click();
  });
  _els.pictureBtn.disabled = true;
  _els.pictureGet = _button(_t("stage.picture.get", "Get the pose-reading model (2.8 GB)"), _getPictureModel);
  _els.pictureGet.hidden = true;
  pictureRow.appendChild(_els.pictureBtn);
  pictureRow.appendChild(_els.pictureGet);
  _els.pictureNote = document.createElement("p");
  _els.pictureNote.className = "stage-hint";
  picture.appendChild(_els.pictureFile);
  picture.appendChild(pictureRow);
  picture.appendChild(_els.pictureNote);
  pose.appendChild(picture);
  panel.appendChild(pose);

  var part = _section(_t("stage.selected", "Selected part"));
  _els.part = document.createElement("p");
  _els.part.className = "stage-part";
  part.appendChild(_els.part);
  var partHint = document.createElement("p");
  partHint.className = "stage-hint";
  partHint.textContent = _t("stage.partHint",
    "Drag a body part to swing it from its joint; hold Shift while dragging to twist it. Drag a figure's ring on the floor to move it; drag the knob on the ring to turn it.");
  part.appendChild(partHint);
  _els.resetPart = _button(_t("stage.resetPart", "Reset this part"), resetPart);
  part.appendChild(_els.resetPart);
  var ringsRow = document.createElement("label");
  ringsRow.className = "stage-check";
  _els.ringsBox = document.createElement("input");
  _els.ringsBox.type = "checkbox";
  _els.ringsBox.id = "stageRings";
  _els.ringsBox.checked = _rings;
  _els.ringsBox.addEventListener("change", function () { setRings(_els.ringsBox.checked); });
  var ringsLabel = document.createElement("span");
  ringsLabel.textContent = _t("stage.rings", "Rotation rings for fine turning");
  ringsRow.appendChild(_els.ringsBox);
  ringsRow.appendChild(ringsLabel);
  part.appendChild(ringsRow);
  panel.appendChild(part);

  var camera = _section(_t("stage.camera", "Camera"));
  var lensRow = document.createElement("label");
  lensRow.className = "stage-lens";
  var lensLabel = document.createElement("span");
  lensLabel.textContent = _t("stage.lens", "Lens");
  _els.lensValue = document.createElement("span");
  _els.lensValue.className = "stage-lens-value";
  var lens = document.createElement("input");
  lens.type = "range"; lens.min = "14"; lens.max = "135"; lens.step = "1"; lens.id = "stageLens";
  lens.value = String(Math.round(_focal));
  lens.addEventListener("input", function () { setFocal(parseFloat(lens.value)); });
  _els.lens = lens;
  lensRow.appendChild(lensLabel); lensRow.appendChild(_els.lensValue); lensRow.appendChild(lens);
  camera.appendChild(lensRow);
  camera.appendChild(_button(_t("stage.frameFigure", "Frame the figure"), function () { if (_mesh) _frameFigure(); }));
  var camHint = document.createElement("p");
  camHint.className = "stage-hint";
  camHint.textContent = _t("stage.cameraHint", "Drag to orbit, right-drag to pan, scroll to zoom. The frame is the picture.");
  camera.appendChild(camHint);
  panel.appendChild(camera);

  var use = _section(_t("stage.use", "Use in ControlNet"));
  var useRow = document.createElement("div");
  useRow.className = "stage-row";
  [1, 2, 3].forEach(function (n) {
    useRow.appendChild(_button(_t("stage.unit." + n, "Unit " + n), function () { _useInUnit(n); }));
  });
  use.appendChild(useRow);
  var useHint = document.createElement("p");
  useHint.className = "stage-hint";
  useHint.textContent = _t("stage.useHint", "The unit renders this scene at the picture's size each time you generate.");
  use.appendChild(useHint);
  panel.appendChild(use);

  var guide = _section(_t("stage.guide", "Drawing guide"));
  _els.guideToggle = _button(_t("stage.guideShow", "Show as a guide on the Canvas"), function () {
    if (_mesh) setGuide(!_guide.on);
  });
  guide.appendChild(_els.guideToggle);
  var guideRow = document.createElement("label");
  guideRow.className = "stage-lens";
  var guideLabel = document.createElement("span");
  guideLabel.textContent = _t("stage.guideOpacity", "Guide strength");
  var guideValue = document.createElement("span");
  guideValue.className = "stage-lens-value";
  var guideSlider = document.createElement("input");
  guideSlider.type = "range"; guideSlider.min = "5"; guideSlider.max = "100"; guideSlider.step = "5";
  guideSlider.id = "stageGuideOpacity";
  guideSlider.value = String(Math.round(_guide.opacity * 100));
  guideValue.textContent = guideSlider.value + "%";
  guideSlider.addEventListener("input", function () {
    guideValue.textContent = guideSlider.value + "%";
    _guide.opacity = parseFloat(guideSlider.value) / 100;
    if (_guide.on) _canvasRedraw();
    _save();
  });
  guideRow.appendChild(guideLabel); guideRow.appendChild(guideValue); guideRow.appendChild(guideSlider);
  guide.appendChild(guideRow);
  var guideHint = document.createElement("p");
  guideHint.className = "stage-hint";
  guideHint.textContent = _t("stage.guideHint",
    "The figures appear faintly over the Canvas for you to draw over. The guide is only on screen: it is not a layer, and it never goes into a picture or a saved file.");
  guide.appendChild(guideHint);
  panel.appendChild(guide);

  var regions = _section(_t("stage.regions", "Regions and masks"));
  regions.appendChild(_button(_t("stage.makeRegions", "Make regions and masks"), function () {
    var made = makeRegionsAndMasks();
    if (made && typeof window.showToast === "function") {
      window.showToast(_t("stage.madeRegions", "Made {regions} regions and {masks} masks from the figures.",
        { regions: made.regions, masks: made.masks }), "success");
    }
  }));
  var marginRow = document.createElement("label");
  marginRow.className = "stage-lens";
  var marginLabel = document.createElement("span");
  marginLabel.textContent = _t("stage.regionMargin", "Region margin");
  var marginValue = document.createElement("span");
  marginValue.className = "stage-lens-value";
  var marginSlider = document.createElement("input");
  marginSlider.type = "range"; marginSlider.min = "0"; marginSlider.max = "15"; marginSlider.step = "1";
  marginSlider.id = "stageRegionMargin";
  marginSlider.value = String(Math.round(_regionMargin * 100));
  marginValue.textContent = marginSlider.value + "%";
  marginSlider.addEventListener("input", function () { marginValue.textContent = marginSlider.value + "%"; });
  marginSlider.addEventListener("change", function () { setRegionMargin(parseFloat(marginSlider.value) / 100); });
  _els.marginSlider = marginSlider; _els.marginValue = marginValue;
  marginRow.appendChild(marginLabel); marginRow.appendChild(marginValue); marginRow.appendChild(marginSlider);
  regions.appendChild(marginRow);
  var regionHint = document.createElement("p");
  regionHint.className = "stage-hint";
  regionHint.textContent = _t("stage.regionsHint",
    "One Regional region per figure, named after it: give each its own prompt in the Regions panel. The regions follow the figures each time you generate. Masks (each figure, its head, its hands) are made from the scene as it is now.");
  var marginHint = document.createElement("p");
  marginHint.className = "stage-hint";
  marginHint.textContent = _t("stage.regionMarginHint",
    "The margin lets a region reach past the body, for hair and clothes that stick out. Paint you add to a region yourself, or erase from it, is kept when it follows its figure.");
  regions.appendChild(regionHint);
  regions.appendChild(marginHint);
  panel.appendChild(regions);

  container.appendChild(_viewport);
  container.appendChild(panel);
  _renderFigureList();
  _select(-1);
}

function _useInUnit(n) {
  var source = document.getElementById("paramCN" + n + "Source");
  if (!source) return;
  source.value = "Stage";
  source.dispatchEvent(new Event("change", { bubbles: true }));
  ["checkCN", "checkCN" + n].forEach(function (id) {
    var box = document.getElementById(id);
    if (box && !box.classList.contains("checked")) box.click();
  });
  if (typeof window.showToast === "function") {
    window.showToast(_t("stage.used." + n, "ControlNet unit " + n + " now uses Stage."), "success");
  }
}

function _attachRenderer() {
  if (!_viewport || !_renderer) return;
  if (_renderer.domElement.parentNode !== _viewport) {
    _viewport.appendChild(_renderer.domElement);
    _viewport.appendChild(_frameEl);
  }
  _els.status.hidden = true;
  if (_els.lens) _els.lens.value = String(Math.round(_focal));
  setFocal(_focal);
}

var _resize = null;
if (window.StudioModules) {
  StudioModules.register("stage", {
    label: "Stage",
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="4.5" r="2"/><path d="M12 7v7"/><path d="M8 10l4-2 4 2"/><path d="M9.5 21l2.5-7 2.5 7"/></svg>',
    init: function (container) {
      console.log(TAG, "Initializing Stage v" + VERSION);
      if (!document.querySelector('link[href*="stage.css"]')) {
        var l = document.createElement("link"); l.rel = "stylesheet";
        l.href = "/studio/static/stage.css?v=" + VERSION; document.head.appendChild(l);
      }
      _container = container;
      _buildUI(container);
      _refreshPictureModel();
      ensureReady().then(function () { _attachRenderer(); _showPictureButton(); }, function () {
        _els.status.textContent = _t("stage.loadFailed", "The figure could not be loaded. The console names the reason.");
      });
    },
    activate: function () {
      _active = true;
      if (!_resize) {
        _resize = function () { _draw(); };
        window.addEventListener("resize", _resize);
      }
      if (_renderer) _attachRenderer();
      _draw();
    },
    deactivate: function () {
      _active = false;
      // Back on the Canvas, the guide shows the pose as it is now.
      if (_guide.on) _canvasRedraw();
    },
  });
} else console.warn(TAG, "StudioModules not available");

window.StudioStage = {
  ensureReady: ensureReady,
  // ST7. Apply a read pose (camera-space joint rotations) to the current figure.
  applyReadPose: applyReadPose,
  pictureModel: function () { return _pictureModel; },
  refreshPictureModel: _refreshPictureModel,
  isReady: function () { return !!_mesh; },
  failed: function () { return _failed; },
  renderMap: renderMap,
  applyPreset: applyPreset,
  // ST6. Body types and heights.
  setBody: function (index, body) { return _mesh ? setBody(index, body) : false; },
  setHeight: function (index, share) { return _mesh ? setHeight(index, share) : false; },
  bodyOf: function (index) { var f = _figures[index]; return f ? { body: f.body, height: f.height, scale: f.scale } : null; },
  mirrorPose: mirrorPose,
  resetPose: resetPose,
  setFocal: setFocal,
  // ST2. Figures, regions and masks.
  addFigure: function () { return _mesh ? !!_addFigure(false) : false; },
  removeFigure: function (index) { return removeFigure(index); },
  selectFigure: function (index) { if (_figures[index]) { _setCurrent(index); _draw(); return true; } return false; },
  figureCount: function () { return _figures.length; },
  // Put figure `index`'s hips at a point in metres (as dragging its pink dot).
  placeFigure: function (index, position) {
    var figure = _figures[index];
    if (!figure) return false;
    figure.boneByName.root.position.fromArray(position);
    figure.boneByName.root.updateMatrixWorld(true);
    _changed(true);
    return true;
  },
  currentFigure: function () { return _current; },
  renderCoverage: renderCoverage,
  makeRegionsAndMasks: makeRegionsAndMasks,
  // ST3. The drawing guide over the Canvas view.
  setGuide: function (on, opacity) { return _mesh ? setGuide(on, opacity) : false; },
  guideOn: function () { return _guide.on; },
  refreshLinkedRegions: refreshLinkedRegions,
  setRegionMargin: setRegionMargin,
  regionMargin: function () { return _regionMargin; },
  // Move a hand or foot ("l_wrist", "r_wrist", "l_foot", "r_foot") to a point
  // in metres, as dragging its handle does.
  reach: function (joint, position) {
    if (!_mesh || !LIMBS[joint]) return false;
    _solveLimb(joint, new T.Vector3().fromArray(position), _worldQuat(_boneByName[joint]));
    _changed(true);
    return true;
  },
  // ST4. Rotation rings on or off (off by default).
  setRings: function (on) { setRings(on); return _rings; },
  // A point in metres to page pixels, through the viewport camera.
  toScreen: function (position) {
    if (!_renderer) return null;
    _camera.updateMatrixWorld(true);
    var rect = _renderer.domElement.getBoundingClientRect();
    var p = new T.Vector3().fromArray(position).project(_camera);
    return [rect.left + (p.x + 1) / 2 * rect.width, rect.top + (1 - p.y) / 2 * rect.height];
  },
  ringOf: function (index) {
    var f = _figures[index];
    if (!f) return null;
    _placeHandles();
    return { ring: f.ring.position.toArray(), knob: f.knob.position.toArray() };
  },
  // Where each handle is drawn, in page pixels (for tests and tours).
  handlesOnScreen: function () {
    if (!_renderer || !_handles.length) return null;
    _placeHandles();
    _camera.updateMatrixWorld(true);
    var rect = _renderer.domElement.getBoundingClientRect(), out = {};
    _handles.forEach(function (h) {
      var p = h.mesh.position.clone().project(_camera);
      out[h.joint] = [rect.left + (p.x + 1) / 2 * rect.width, rect.top + (1 - p.y) / 2 * rect.height];
    });
    return out;
  },
  jointPosition: function (joint) {
    if (!_mesh || !_boneByName[joint]) return null;
    _mesh.updateMatrixWorld(true);
    return _worldPos(_boneByName[joint]).toArray();
  },
  // The camera as numbers: eye and target in metres, lens in mm.
  getView: function () {
    return _camera ? { position: _camera.position.toArray(), target: _orbit.target.toArray(), focal: _focal } : null;
  },
  setView: function (view) {
    if (!_camera || !view) return false;
    _camera.position.fromArray(view.position);
    _orbit.target.fromArray(view.target);
    _orbit.update();
    if (view.focal) setFocal(view.focal);
    _changed(true);
    return true;
  },
  onChange: onChange,
  open: function () { try { if (window.StudioModules) StudioModules.activate("stage"); } catch (_) { /* no tab */ } },
};

// A unit already on Stage when this script arrives (a remembered session):
// load the figure now, so the first Generate has its map.
if ([1, 2, 3].some(function (n) {
  var source = document.getElementById("paramCN" + n + "Source");
  return source && source.value === "Stage";
})) {
  ensureReady().catch(function () { /* the panel reports it at Generate */ });
  if (typeof ControlNetPanel !== "undefined" && ControlNetPanel._watchStage) ControlNetPanel._watchStage();
}
})();
