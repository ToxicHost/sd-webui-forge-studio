"""Forge loaded-model registry contract, and why it is not the Tier-0 residual.

Attempt 06 reproduced the Tier-0 image byte-for-byte and left 9,568,256 bytes
allocated -- the same figure, to the byte, as attempt 05, after releasing strictly
more. The attempt-06 report named `backend.memory_management.current_loaded_models`
as the candidate owner.

Source rejects that candidate. A `LoadedModel` holds a **weak** reference to its
`ModelPatcher` (`backend/memory_management.py:446`) and a **weak** reference to
the underlying module (`:490`). Its only strong state is a device, a bool, and
two `weakref.finalize` handles -- and `finalize` holds the callback, not the
object. A registry entry therefore cannot keep a UNet, VAE, text encoder, or any
device tensor alive, and popping one frees nothing by itself.

These tests pin that contract so the rejection cannot go quietly stale: if Forge
switches the registry back to strong references, moves the single insertion site,
gains a bulk drain, or if Studio starts touching the registry, this suite fails.

They also pin the two facts that make Forge's native teardown unusable under the
one-cache-clear policy: `unload_all_models()` takes no argument, and the clear it
performs cannot be suppressed by any caller.

Nothing here imports `backend.memory_management` -- that module runs device
discovery at import. Every claim about Forge is read from source by AST. The
retention proofs use synthetic objects and a liveness-driven fake allocator.

No CUDA tensor is allocated. No model file is opened. No image is generated.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE.
"""

from __future__ import annotations

import ast
import gc
import sys
import unittest
import weakref
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

MEMORY_MANAGEMENT = APP_ROOT / "backend" / "memory_management.py"
LOADER = APP_ROOT / "backend" / "loader.py"
ANIMA = APP_ROOT / "backend" / "diffusion_engine" / "anima.py"
SAMPLING_FUNCTION = APP_ROOT / "backend" / "sampling" / "sampling_function.py"
VAE_PATCHER = APP_ROOT / "backend" / "patcher" / "vae.py"
SD_MODELS = APP_ROOT / "modules" / "sd_models.py"
FAILURE_CLEANUP = APP_ROOT / "forge_headless" / "failure_cleanup.py"
CONTROLLED_DEVICE = APP_ROOT / "forge_headless" / "controlled_device.py"
PROBE = APP_ROOT / "scripts" / "headless" / "first_image_probe.py"

#: Declared so a loader error cannot silently hide this suite.
EXPECTED_REGISTRY_CONTRACT_TESTS = 40

SCOPE_LABELS = ("STATIC_IMPORT_SCOPE", "MINIMAL_RUNTIME_SCOPE")

REGISTRY_NAME = "current_loaded_models"

#: Studio-owned trees that must stay out of Forge's memory manager.
STUDIO_TREES = ("forge_headless", "forge_studio", "scripts/headless")

#: Names that would mean Studio had reached into the registry or its teardown.
REGISTRY_SYMBOLS = (
    REGISTRY_NAME,
    "LoadedModel",
    "unload_all_models",
    "unload_model_weights",
    "free_memory",
    "soft_empty_cache",
    "torch_gc",
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(tree: ast.Module, name: str, *, cls: str | None = None) -> ast.FunctionDef:
    """Return one function by name, optionally scoped to a class body."""

    scope: list[ast.stmt]
    if cls is None:
        scope = list(tree.body)
    else:
        found = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls]
        if not found:
            raise AssertionError(f"class {cls} not found")
        scope = list(found[0].body)

    for node in scope:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found in {cls or 'module scope'}")


def _enclosing_function(tree: ast.Module, node: ast.AST) -> str | None:
    """Name of the innermost function containing `node`, or None."""

    best: tuple[int, str] | None = None
    for candidate in ast.walk(tree):
        if not isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = candidate.lineno
        end = getattr(candidate, "end_lineno", start)
        if start <= node.lineno <= end and (best is None or start > best[0]):
            best = (start, candidate.name)
    return None if best is None else best[1]


def _calls(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _registry_references(tree: ast.Module) -> list[ast.Name]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id == REGISTRY_NAME
    ]


def _studio_python_files() -> list[Path]:
    files: list[Path] = []
    for tree_name in STUDIO_TREES:
        root = APP_ROOT / Path(tree_name)
        if not root.exists():
            raise AssertionError(f"expected Studio tree missing: {tree_name}")
        files.extend(sorted(root.rglob("*.py")))
    return files


class _Sentinel:
    """A weak-referenceable stand-in for a device-owning model object."""


class FakeAllocator:
    """Liveness-driven byte counter.

    Bytes fall only when the object they were charged to is actually collected,
    so a test cannot pass by clearing a cache or by asserting its own intent.
    """

    def __init__(self) -> None:
        self._live: dict[int, int] = {}
        self._keepalive: list[weakref.finalize] = []

    def charge(self, obj: object, nbytes: int) -> None:
        key = id(obj)
        self._live[key] = self._live.get(key, 0) + nbytes
        self._keepalive.append(weakref.finalize(obj, self._live.pop, key, None))

    def allocated(self) -> int:
        gc.collect()
        return sum(value for value in self._live.values() if value)


class WeakLoadedModel:
    """The discovered `LoadedModel` retention contract, modelled exactly.

    Weak to the patcher (memory_management.py:446) and weak to the module
    (:490); strong only to a device, a flag, and finalizer handles.
    """

    def __init__(self, patcher: object, device: str = "cuda:0") -> None:
        self._model = weakref.ref(patcher)
        self.device = device
        self.real_model = None
        self.currently_used = True
        self.model_finalizer = None

    @property
    def model(self) -> object | None:
        return self._model()

    def model_load(self, real_model: object) -> None:
        self.real_model = weakref.ref(real_model)


class StrongLoadedModel:
    """The contract Forge does *not* have -- the negative control."""

    def __init__(self, patcher: object, device: str = "cuda:0") -> None:
        self.model = patcher
        self.device = device
        self.real_model = None

    def model_load(self, real_model: object) -> None:
        self.real_model = real_model


class RegistryDeclarationTests(unittest.TestCase):
    """The registry itself: one list, one writer, no bulk drain."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = _parse(MEMORY_MANAGEMENT)

    def test_registry_is_a_module_level_list(self):
        found = [
            node
            for node in self.tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == REGISTRY_NAME
        ]
        self.assertEqual(
            len(found), 1,
            f"{REGISTRY_NAME} must be declared exactly once at module scope",
        )
        self.assertIsInstance(
            found[0].value, ast.List,
            f"{REGISTRY_NAME} must be initialised to a list literal",
        )
        self.assertEqual(
            found[0].value.elts, [],
            f"{REGISTRY_NAME} must start empty",
        )

    def test_exactly_one_insertion_site_and_it_is_load_models_gpu(self):
        inserts = [
            call
            for call in _calls(self.tree)
            if isinstance(call.func, ast.Attribute)
            and call.func.attr in {"insert", "append", "extend"}
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == REGISTRY_NAME
        ]
        self.assertEqual(
            len(inserts), 1,
            "the registry must have exactly one writer; a second one changes "
            "which call sites can create entries",
        )
        self.assertEqual(inserts[0].func.attr, "insert")
        self.assertEqual(
            _enclosing_function(self.tree, inserts[0]), "load_models_gpu",
        )

    def test_no_bulk_drain_operation_exists(self):
        for call in _calls(self.tree):
            if (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == REGISTRY_NAME
            ):
                self.assertNotEqual(
                    call.func.attr, "clear",
                    "a .clear() on the registry would change the teardown "
                    "contract this milestone documented",
                )

        for node in ast.walk(self.tree):
            if isinstance(node, ast.Delete):
                for target in node.targets:
                    self.assertFalse(
                        isinstance(target, ast.Name) and target.id == REGISTRY_NAME,
                        "the registry must not be deleted wholesale",
                    )
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Subscript) and isinstance(
                        target.value, ast.Name
                    ):
                        self.assertNotEqual(
                            target.value.id, REGISTRY_NAME,
                            "slice-assignment would be an undocumented bulk drain",
                        )

    def test_registry_is_never_rebound_after_declaration(self):
        rebinds = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == REGISTRY_NAME
                for t in node.targets
            )
        ]
        self.assertEqual(
            rebinds, [],
            "rebinding the registry name would orphan every entry silently",
        )

    def test_every_removal_is_a_gated_pop_in_a_known_function(self):
        pops = [
            call
            for call in _calls(self.tree)
            if isinstance(call.func, ast.Attribute)
            and call.func.attr == "pop"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == REGISTRY_NAME
        ]
        self.assertTrue(pops, "the registry must have removal sites")
        owners = {_enclosing_function(self.tree, call) for call in pops}
        self.assertEqual(
            owners,
            {
                "free_memory",
                "load_models_gpu",
                "cleanup_models_gc",
                "cleanup_models",
                "unload_model",
            },
            "the set of functions that remove registry entries changed",
        )


class EntryRetentionTests(unittest.TestCase):
    """The finding this milestone turns on: entries retain nothing strongly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = _parse(MEMORY_MANAGEMENT)

    def _loaded_model(self) -> ast.ClassDef:
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "LoadedModel":
                return node
        raise AssertionError("class LoadedModel not found")

    def test_patcher_reference_is_weak(self):
        set_model = _function(self.tree, "_set_model", cls="LoadedModel")
        assigns = [
            node
            for node in ast.walk(set_model)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Attribute) and t.attr == "_model"
                for t in node.targets
            )
        ]
        self.assertEqual(len(assigns), 1, "_model must be assigned exactly once")
        call = assigns[0].value
        self.assertIsInstance(call, ast.Call)
        self.assertEqual(
            _call_name(call), "ref",
            "the patcher must be held by weakref.ref; a strong reference here "
            "would make the registry a genuine retention candidate again",
        )

    def test_real_model_reference_is_weak(self):
        model_load = _function(self.tree, "model_load", cls="LoadedModel")
        assigns = [
            node
            for node in ast.walk(model_load)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Attribute) and t.attr == "real_model"
                for t in node.targets
            )
        ]
        self.assertEqual(len(assigns), 1)
        self.assertEqual(_call_name(assigns[0].value), "ref")

    def test_entry_stores_no_strong_model_attribute(self):
        init = _function(self.tree, "__init__", cls="LoadedModel")
        params = {arg.arg for arg in init.args.args}
        for node in ast.walk(init):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not (isinstance(target, ast.Attribute) and target.attr != "_model"):
                    continue
                value = node.value
                if isinstance(value, ast.Name) and value.id in params:
                    self.assertNotEqual(
                        value.id, "model",
                        "LoadedModel.__init__ must not bind the patcher strongly",
                    )

    def test_model_is_a_property_over_the_weakref(self):
        prop = _function(self.tree, "model", cls="LoadedModel")
        decorators = {
            d.id for d in prop.decorator_list if isinstance(d, ast.Name)
        }
        self.assertIn("property", decorators)
        names = {
            node.attr
            for node in ast.walk(prop)
            if isinstance(node, ast.Attribute)
        }
        self.assertIn(
            "_model", names,
            "the .model property must dereference the weakref, not a stored object",
        )

    def test_finalizer_prunes_the_registry_when_the_model_dies(self):
        model_load = _function(self.tree, "model_load", cls="LoadedModel")
        finalizes = [
            call for call in _calls(model_load) if _call_name(call) == "finalize"
        ]
        self.assertEqual(
            len(finalizes), 1,
            "model_load must register exactly one finalizer on the real model",
        )
        callbacks = [
            arg.id for arg in finalizes[0].args if isinstance(arg, ast.Name)
        ]
        self.assertIn(
            "cleanup_models", callbacks,
            "the finalizer must prune the registry; this is what makes stale "
            "entries self-clearing rather than retaining",
        )

    def test_entry_identity_is_by_underlying_patcher(self):
        eq = _function(self.tree, "__eq__", cls="LoadedModel")
        attrs = {
            node.attr for node in ast.walk(eq) if isinstance(node, ast.Attribute)
        }
        self.assertEqual(
            attrs, {"model"},
            "__eq__ must compare the dereferenced patcher; this is the only "
            "ownership discriminator available to Studio",
        )
        self.assertTrue(
            any(isinstance(node, ast.Is) for node in ast.walk(eq)),
            "__eq__ must use identity, not equality",
        )

    def test_model_unload_detaches_the_patcher(self):
        unload = _function(self.tree, "model_unload", cls="LoadedModel")
        self.assertTrue(
            any(_call_name(call) == "detach" for call in _calls(unload)),
            "model_unload must call ModelPatcher.detach -- that, not the pop, "
            "is what moves weights off the device",
        )


class NativeTeardownContractTests(unittest.TestCase):
    """`unload_all_models` offers the caller no lever, and always clears."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = _parse(MEMORY_MANAGEMENT)

    def test_unload_all_models_takes_no_arguments(self):
        fn = _function(self.tree, "unload_all_models")
        args = fn.args
        self.assertEqual(
            (args.args, args.posonlyargs, args.kwonlyargs, args.vararg, args.kwarg),
            ([], [], [], None, None),
            "a parameter here would be the suppression lever this milestone "
            "looked for and did not find",
        )

    def test_unload_all_models_is_a_single_free_memory_call(self):
        fn = _function(self.tree, "unload_all_models")
        body = [n for n in fn.body if not isinstance(n, ast.Expr) or not isinstance(n.value, ast.Constant)]
        self.assertEqual(len(body), 1, "unload_all_models must stay a one-liner")
        calls = _calls(body[0])
        self.assertIn("free_memory", {_call_name(c) for c in calls})

    def test_free_memory_has_no_cache_control_parameter(self):
        fn = _function(self.tree, "free_memory")
        names = [arg.arg for arg in fn.args.args + fn.args.kwonlyargs]
        self.assertEqual(names, ["memory_required", "device", "keep_loaded"])
        for name in names:
            self.assertNotIn("cache", name)
            self.assertNotIn("empty", name)

    def test_free_memory_clears_the_cache_in_both_branches(self):
        fn = _function(self.tree, "free_memory")
        clears = [c for c in _calls(fn) if _call_name(c) == "soft_empty_cache"]
        self.assertGreaterEqual(
            len(clears), 2,
            "free_memory clears in the unloaded branch and in the else branch; "
            "both must remain accounted for",
        )

    def test_soft_empty_cache_never_reads_its_force_parameter(self):
        fn = _function(self.tree, "soft_empty_cache")
        self.assertEqual([a.arg for a in fn.args.args], ["force"])
        reads = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Name) and n.id == "force"
        ]
        self.assertEqual(
            reads, [],
            "`force` is dead in the body -- it neither forces nor suppresses, "
            "so it is not a lever either",
        )

    def test_soft_empty_cache_is_the_only_first_party_empty_cache(self):
        owners: set[str | None] = set()
        for call in _calls(self.tree):
            if isinstance(call.func, ast.Attribute) and call.func.attr == "empty_cache":
                owners.add(_enclosing_function(self.tree, call))
        self.assertEqual(
            owners, {"soft_empty_cache"},
            "a second empty_cache owner in the memory manager would change the "
            "cache-clear accounting",
        )

    def test_the_no_clear_removal_path_also_does_no_unloading(self):
        fn = _function(self.tree, "unload_model")
        names = {_call_name(c) for c in _calls(fn)}
        self.assertNotIn("soft_empty_cache", names)
        self.assertNotIn(
            "model_unload", names,
            "unload_model pops without unloading; using it as a 'no clear' "
            "teardown would report success and move no weights",
        )


class RegistrationPointTests(unittest.TestCase):
    """Registration is lazy and per component, not at load."""

    def test_the_loader_never_registers(self):
        tree = _parse(LOADER)
        names = {_call_name(c) for c in _calls(tree)}
        self.assertNotIn("load_models_gpu", names)
        self.assertNotIn(
            "load_model_gpu", names,
            "if the loader starts registering, entries would exist before the "
            "first generation and the trace in evidence would be wrong",
        )

    def test_text_encoder_registers_from_the_engine_conditioning_call(self):
        tree = _parse(ANIMA)
        fn = _function(tree, "get_learned_conditioning", cls="Anima")
        self.assertIn("load_model_gpu", {_call_name(c) for c in _calls(fn)})

    def test_unet_registers_from_sampling_prepare(self):
        tree = _parse(SAMPLING_FUNCTION)
        fn = _function(tree, "sampling_prepare")
        self.assertIn("load_models_gpu", {_call_name(c) for c in _calls(fn)})

    def test_vae_registers_from_its_own_patcher(self):
        tree = _parse(VAE_PATCHER)
        calls = [c for c in _calls(tree) if _call_name(c) == "load_models_gpu"]
        self.assertTrue(
            calls, "the VAE must still register itself on decode/encode",
        )


class NeoUnloadSequenceTests(unittest.TestCase):
    """Neo's teardown, for the comparison the evidence draws."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = _parse(SD_MODELS)

    def test_unload_model_weights_drains_the_registry_then_clears_again(self):
        fn = _function(self.tree, "unload_model_weights")
        sequence = [_call_name(c) for c in _calls(fn)]
        self.assertEqual(
            sequence.count("unload_all_models"), 1,
            "Neo's unload must still be the registry-drain reference point",
        )
        self.assertIn("soft_empty_cache", sequence)
        self.assertIn("collect", sequence)

    def test_neos_own_path_is_not_single_cache_clear(self):
        fn = _function(self.tree, "unload_model_weights")
        names = [_call_name(c) for c in _calls(fn)]
        self.assertLess(
            names.index("unload_all_models"), names.index("soft_empty_cache"),
            "unload_all_models clears internally, and Neo then clears again -- "
            "so Neo is not a single-clear model for Studio to copy",
        )

    def test_no_generation_path_calls_the_unload(self):
        processing = _parse(APP_ROOT / "modules" / "processing.py")
        inner = _function(processing, "process_images_inner")
        self.assertNotIn(
            "unload_model_weights", {_call_name(c) for c in _calls(inner)},
        )


class StudioBoundaryTests(unittest.TestCase):
    """Studio owns exactly one cache clear and touches no Forge memory state."""

    def test_studio_code_never_references_the_registry_or_its_teardown(self):
        offenders: list[str] = []
        for path in _studio_python_files():
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
            names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            names |= {
                n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
            }
            hit = sorted(names & set(REGISTRY_SYMBOLS))
            if hit:
                offenders.append(f"{path.relative_to(APP_ROOT)}: {hit}")
        self.assertEqual(
            offenders, [],
            "Studio-owned code must not reach into Forge's memory manager; the "
            "registry teardown was deliberately not integrated",
        )

    def test_failure_cleanup_performs_no_device_call(self):
        tree = _parse(FAILURE_CLEANUP)
        names = {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
        }
        for forbidden in ("empty_cache", "ipc_collect", "synchronize", "cuda"):
            self.assertNotIn(
                forbidden, names,
                "release must stay measurable on its own; a clear inside it "
                "would make the before/after samples meaningless",
            )

    def test_studio_owns_exactly_one_cache_clear(self):
        sites: list[str] = []
        for path in _studio_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for call in _calls(tree):
                if isinstance(call.func, ast.Attribute) and call.func.attr == "empty_cache":
                    sites.append(
                        f"{path.relative_to(APP_ROOT)}:"
                        f"{_enclosing_function(tree, call)}"
                    )
        self.assertEqual(
            len(sites), 1,
            f"exactly one intentional cache clear is allowed; found {sites}",
        )
        self.assertTrue(sites[0].endswith(":release_cuda_cache"))

    def test_the_probe_samples_between_release_and_clear(self):
        tree = _parse(PROBE)
        release = clear = None
        for call in _calls(tree):
            name = _call_name(call)
            if name == "release_generation_references" and release is None:
                release = call.lineno
            if name == "release_cuda_cache":
                clear = call.lineno
        self.assertIsNotNone(release)
        self.assertIsNotNone(clear)

        samples = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Attribute) and t.attr == "after_release"
                for t in node.targets
            )
        ]
        self.assertEqual(len(samples), 1)
        self.assertLess(release, samples[0])
        self.assertLess(
            samples[0], clear,
            "allocated must be sampled after the release and before the clear, "
            "or an emptied cache could be mistaken for a released reference",
        )


class WeakRegistryRetentionTests(unittest.TestCase):
    """Synthetic proof, with a negative control, that weak entries retain nothing."""

    def test_a_weak_entry_does_not_keep_its_patcher_alive(self):
        patcher = _Sentinel()
        witness = weakref.ref(patcher)
        registry = [WeakLoadedModel(patcher)]

        del patcher
        gc.collect()

        self.assertIsNone(witness(), "the weak entry must not retain the patcher")
        self.assertIsNone(registry[0].model)

    def test_a_strong_entry_does_keep_it_alive(self):
        patcher = _Sentinel()
        witness = weakref.ref(patcher)
        registry = [StrongLoadedModel(patcher)]

        del patcher
        gc.collect()

        self.assertIsNotNone(
            witness(),
            "negative control: the test can detect retention when it exists, "
            "so the previous result is a finding and not a blind spot",
        )
        del registry
        gc.collect()
        self.assertIsNone(witness())

    def test_the_fake_allocator_is_liveness_driven(self):
        allocator = FakeAllocator()
        held = _Sentinel()
        allocator.charge(held, 9_568_256)
        self.assertEqual(allocator.allocated(), 9_568_256)

        del held
        self.assertEqual(
            allocator.allocated(), 0,
            "bytes must fall only on collection; a counter that can be zeroed "
            "any other way would prove nothing",
        )

    def test_popping_a_weak_entry_frees_nothing_by_itself(self):
        patcher = _Sentinel()
        allocator = FakeAllocator()
        allocator.charge(patcher, 9_568_256)
        registry = [WeakLoadedModel(patcher)]

        registry.pop()
        gc.collect()
        self.assertEqual(
            allocator.allocated(), 9_568_256,
            "draining a weak registry does not move bytes -- this is the "
            "source-level reason attempt 06's candidate is rejected",
        )

        del patcher
        self.assertEqual(
            allocator.allocated(), 0,
            "the bytes belong to whoever holds the patcher, not to the registry",
        )

    def test_a_strong_registry_would_have_held_the_bytes(self):
        patcher = _Sentinel()
        allocator = FakeAllocator()
        allocator.charge(patcher, 9_568_256)
        registry = [StrongLoadedModel(patcher)]

        del patcher
        self.assertEqual(
            allocator.allocated(), 9_568_256,
            "the counterfactual: had Forge held strong references, the residual "
            "would have survived exactly this way",
        )

        registry.pop()
        self.assertEqual(allocator.allocated(), 0)


class OwnershipDiscriminatorTests(unittest.TestCase):
    """If a teardown is ever authorised, this is the only discriminator available."""

    def test_identity_comparison_distinguishes_owned_entries(self):
        owned = _Sentinel()
        foreign = _Sentinel()
        registry = [WeakLoadedModel(owned), WeakLoadedModel(foreign)]

        mine = [e for e in registry if e.model is owned]
        self.assertEqual(len(mine), 1)
        self.assertIsNot(mine[0].model, foreign)

    def test_a_dead_entry_is_unattributable_and_must_be_guarded(self):
        patcher = _Sentinel()
        entry = WeakLoadedModel(patcher)
        del patcher
        gc.collect()

        self.assertIsNone(entry.model)
        other = WeakLoadedModel(_Sentinel())
        gc.collect()
        self.assertIsNone(other.model)
        self.assertIs(
            entry.model, other.model,
            "two dead entries both dereference to None, so an identity test "
            "without a None guard would match the wrong entry",
        )


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_tests_are_discovered(self):
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(), EXPECTED_REGISTRY_CONTRACT_TESTS,
            "Registry contract test count changed: update "
            "EXPECTED_REGISTRY_CONTRACT_TESTS deliberately, never to match",
        )

    def test_scope_is_declared(self):
        doc = sys.modules[__name__].__doc__ or ""
        for label in SCOPE_LABELS:
            self.assertIn(label, doc)

    def test_this_suite_never_imports_the_memory_manager(self):
        """Asserted against this file's own imports, not against sys.modules.

        A sys.modules check would pass or fail depending on which other suite
        ran first, which is a worse guard than none.
        """

        tree = _parse(Path(__file__).resolve())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for name in imported:
            self.assertFalse(
                name == "backend" or name.startswith("backend."),
                f"{name} must not be imported: the memory manager runs device "
                "discovery at import, so this suite reads it as source instead",
            )


if __name__ == "__main__":
    unittest.main()
