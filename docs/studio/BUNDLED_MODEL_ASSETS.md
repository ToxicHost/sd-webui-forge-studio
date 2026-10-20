# Bundled model assets — provenance, hashes, licences, attribution

Model weights Studio ships as release assets, so that no generation ever needs
a download. Every entry records what it is, where it came from, exactly which
bytes, and under what terms.

For Git installations, scripts/setup_assets.py obtains these same exact files
from the repository's studio-assets-v1 release during setup. Existing valid files
are reused without network access. Catalogue reads and generation stay local;
see [GIT_INSTALL.md](GIT_INSTALL.md).

The weights themselves are **not in git** (`.gitignore:19` excludes
`/models/**/*`). This file is the tracked record of what a release bundle must
contain and what it must be verified against.

Studio's detector catalogue admits an asset only when it is **locally present
AND hash-valid**. A file whose hash does not match this record is not offered,
rather than being offered and failing later.

---

## ADetailer detectors — `models/adetailer/`

```text
source     https://huggingface.co/Bingsu/adetailer
revision   53cc19de382014514d9d4038601d261a7faa9b7b
licence    Apache-2.0        (verified from repo metadata, not assumed:
                             cardData.license == "apache-2.0",
                             tag "license:apache-2.0")
```

The five ADetailer ships by default. Both inference paths are represented, which
is deliberate — bbox detection and segmentation are separate code paths and each
has to be proven:

```text
sha256                                                            bytes     file                    path
70b640f8f60b1cf0dcc72f30caf3da9495eb2fb6509da48c53374ad6806e6a9c   6230011  face_yolov8n.pt         bbox
c7237eff25787377de196961140ceaed324d859ee8de5a775d93d33a0e3fab78  22507707  face_yolov8s.pt         bbox
3991202eb69e9ddcb3b9ba80cdeb41e734ffaf844403d6c9f47d515cd88c6f29   6237883  hand_yolov8n.pt         bbox
38fc8aaae97cb6e70be4ec44770005b26ed473471362afcda62a0037d7ccf432   6777003  person_yolov8n-seg.pt   segmentation
53c54aec2239355faffc6c5b70d0f3d05042f386f956cbec39cec46ad456f050  23850731  person_yolov8s-seg.pt   segmentation
                                                                  ─────────
                                                                  65603285  (62.6 MiB)
```

**Note the licence split.** The `adetailer` CODE is AGPL-3.0; these WEIGHTS are
Apache-2.0, from a different repository. They are separate obligations and are
recorded separately so neither is assumed from the other.

Not bundled, available from the same repo if wanted later: `face_yolov8n_v2`,
`hand_yolov8s`, `deepfashion2_yolov8s-seg`, `face_yolov8m`, `face_yolov9c`,
`hand_yolov9c`, `person_yolov8m-seg`. Adding one is a drop-in plus a row here.

`yolov8x-worldv2.pt` appears in upstream ADetailer's default list but is **not
in this repository** — it is a YOLO-World open-vocabulary model sourced
elsewhere. It is deliberately not bundled, and the catalogue will not offer it.

---

## MobileSAM — `models/sam/` (word masks, M2)

```text
file       mobile_sam.pt
sha256     6dbb90523a35330fedd7f1d3dfc66f995213d81b29a5ca8108dbcdd4e37d6c2f
bytes      40728226  (38.8 MiB)
source     https://github.com/ChaoningZhang/MobileSAM, weights/mobile_sam.pt
licence    Apache-2.0  (the repository's LICENSE, confirmed by the owner
                        2026-10-15; no NOTICE file)
```

**Provenance, checked byte for byte (2026-10-15).** The file was taken from
Ultralytics' asset release (`ultralytics/assets` `v8.3.0`, the name its SAM
loader resolves) for the prompt-masks research. Its git blob hash,
`7ef2d090979fd9853adf17b7a99c8b94a1c5a6a7`, and its size are exactly those the
GitHub API lists for `weights/mobile_sam.pt` in the MobileSAM repository: the
same bytes as upstream, not a repackaging.

**Attribution.** MobileSAM (Chaoning Zhang et al.), built on Meta's Segment
Anything (Apache-2.0) and TinyViT (MIT). Studio loads it on the CPU through
`forge_headless/ultralytics_boundary.py` `import_sam` (offline), and refuses
to build without this file rather than letting Ultralytics download one.

---

## Upscaler — `models/ESRGAN/`

```text
file       remacri_original.pt
sha256     e1a73bd89c2da1ae494774746398689048b5a892bd9653e146713f9df8bca86a
bytes      67025055  (63.9 MiB)
```

Identified by loading it, rather than by trusting the filename:

```text
architecture      ESRGAN        (via spandrel)
native scale      4x
channels          3 -> 3
supports_half     True
```

**Attribution.** **Remacri**, a community-trained ESRGAN upscaling model by
*FoolhardyVEVO*, distributed through the community upscaler model
collections. Credited in Studio's Credits (Settings › About) from 2026-10-20;
the earlier claim that it already was had no Credits line behind it.

**Licence: treated as CC BY-NC-SA 4.0, and kept.** The weights carry no
embedded licence. Checked 2026-10-20: the Hugging Face source
`FacehugmanIII/4x_foolhardy_Remacri` states no licence ("unknown"), and
OpenModelDB (`openmodeldb.info/models/4x-Remacri`) lists it as
**CC BY-NC-SA 4.0**. Decided 2026-10-20: Studio meets those terms and keeps
it as the default upscaler. Attribution: credited in Credits. Non-commercial:
Forge Studio is free software and will never be sold. Share-alike: the
weights ship unmodified. A commercial redistribution of Studio would have to
replace it (THIRD_PARTY_NOTICES.md).

A filename gotcha worth keeping: Neo derives an upscaler's SCALE from its
FILENAME (`re.search(r"(\d)[xX]|[xX](\d)")`, defaulting to 4 on no match).
`remacri_original` contains no digits, so it takes the default 4 — which is
correct here by luck, not by declaration. A 2x model named this way would be
registered as 4x and produce wrong dimensions.

---

## What this record is for

```text
release        the bundle must contain exactly these files, at these hashes
verification   the catalogue validates hash before offering an asset
licence        Apache-2.0 (detectors) and the Remacri credit are obligations
               that travel with distribution, not optional courtesies
no runtime downloads   catalogue reads/generation never fetch weights. Source
                       setup may download missing, pinned assets before launch.
```
