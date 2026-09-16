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

**Attribution.** Credited in Studio's Credits as **Remacri**, a
community-trained ESRGAN upscaling model, widely attributed to *FoolhardyVEVO*
and distributed through the community upscaler model collections.

**Licence status: owner-asserted, not verified from a canonical source.** The
weights carry no embedded licence, and no authoritative licence statement was
located for them. The owner has reviewed this and approved bundling with
credit. Recorded here as asserted rather than verified so that the distinction
survives — if a canonical licence is later found, tighten this entry rather
than discovering the gap during a release review.

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
