"""
Turn the Roboflow P&ID export into a trainable YOLO dataset.

The raw export has 180 classes over 27,541 annotations, and it is not usable
as-is:

  * The same object appears under several names. PSV (636), PRV (195),
    "Relief Valve" (65) and "pressure safety Valve" (2) are one device. A model
    trained on that learns to split its evidence four ways and is confident
    about none of them.
  * Typos are separate classes: "Axical COMP" vs "Axial Compressor",
    "Level Trasmitter" vs "Level Transmitter", "bolier", "Vaccum Pump",
    "Fluidzed Recactor", "temp trasmitter".
  * 75 classes have fewer than 20 examples. Three have one. Nothing can be
    learned from those, but they still cost the model capacity and pollute the
    confusion matrix.
  * Class 0, "Piping-Valves-Equipment-ID", is Roboflow's supercategory
    placeholder, not a symbol at all.

So we merge by FUNCTION, which is both what a plant engineer cares about and
what the model can actually see.

A note on actuators. The export mixes body type with actuation:
"Gate Valve", "Motor Operated Gate Valve", "Hand Operated Gate Valve",
"Pneumatic-Diaphragm Gate Valve" are four classes for one body with different
things bolted on top. We merge to body type. The actuator is a separate glyph
drawn above the valve and is better recovered as its own detection (the export
already has "Motor", "pneumatic", "Solenoid..." classes) than by multiplying
every valve type by every actuator.

    python -m tools.pid_dataset --src "/path/to/P-ID Symbols.v1i.coco"
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "data" / "pid_yolo"

# Anything with fewer than this many annotations after merging is dropped:
# a detector cannot learn a class from a handful of boxes.
MIN_EXAMPLES = 25

# Not symbols - Roboflow's placeholder supercategory.
DROP = {"Piping-Valves-Equipment-ID"}

# ---------------------------------------------------------------------------
# merge map: raw Roboflow name -> our class
# Grouped by what an engineer asks about, not by drawing trivia.
# ---------------------------------------------------------------------------
MERGE: dict[str, str] = {}


def _add(target: str, *names: str) -> None:
    for n in names:
        MERGE[n] = target


# --- relief devices: safety critical, must never be confused with a valve ---
_add("relief_valve",
     "PSV", "PRV", "Relief Valve", "pressure safety Valve",
     "Pressure Balance Diaphragm", "Angle Blowdown", "Bleeder Valve")

# --- valve bodies (actuation merged in; see module docstring) ---
_add("gate_valve",
     "Gate Valve", "Pneumatic-Diaphragm Gate Valve", "Hand Operated Gate Valve",
     "Motor Operated Gate Valve", "Powered Gate Valve", "Closed Gate Valve",
     "Solenoid Operated Gate Valve", "Hydraulic Operated Gate Valve",
     "Rotary Piston-Pneumatic Gate Valve", "3 Way Gate Valve",
     "4 Way Gate Valve", "Balance Diaphragm Gate Valve", "Motor closed Gate Valve",
     "Rotary Piston-Pneumatic Closed Gate Valve", "Electric operator gate valve",
     "Knife Valve", "Slide Valve", "Stop Valve", "Integrated Block Valve")
_add("ball_valve",
     "Ball Valve", "Hydraulic Operated Ball Valve", "Hand Operated Ball Valve",
     "Closed Ball Valve", "3 Way Ball Valve", "4 Way Ball Valve",
     "Pneumatic-Diaphragm Ball Valve", "Flanged Ball",
     "Quarter Turn Valve Double Acting", "Quarter Turn Valve Spring Acting")
_add("globe_valve",
     "Globe Valve", "Pneumatic-Diaphragm Globe Valve", "Hand Operated Globe Valve",
     "Hydraulic Operated Globe Valve", "Motor Operated Globe Valve",
     "Cock Globe Valve", "3 Way Globe Valve", "Flanged Globe", "Angle GlobeValve")
_add("butterfly_valve",
     "Butterfly Valve", "Pneumatic-Diaphragm Butterfly Valve",
     "Motor Operated Butterfly Valve", "Rotary Piston-Pneumatic Butterfly Valve",
     "Flanged Butterfly")
_add("check_valve", "Check Valve", "Float Operated Valve")
_add("needle_valve", "Needle Valve")
_add("plug_valve", "Plug Valve", "Motor Operated Plug Valve", "Cock Valve",
     "Flanged Cock", "Rotary Valve")
_add("diaphragm_valve", "Diaphragm Valve", "Motor Operated Diaphragm Valve",
     "Pinch Valve")
_add("angle_valve", "Angle Valve", "Motor Operated Angle Valve",
     "Hand Operated Angle Valve", "Pneumatic-Diaphragm Angle Valve")
_add("control_valve", "Pneumatic-Diaphragm 3 Way Valve", "Piston Operated Valve",
     "Solenoid Closed Valve", "Hydraulic Closed Valve", "Pressure Regulator",
     "BackPressure Regulator")

# --- actuators drawn as their own glyph above a valve ---
_add("actuator", "Motor", "Electric Motor", "pneumatic", "Diesel Motor",
     "Fail Closed Safe Position", "Fail Open Safe Position",
     "Fail Indeterminate Safe Position", "Fail Lock Safe Position")

# --- rotating equipment ---
_add("pump",
     "Centrifugal Pump", "Gear Pump", "Vertical Pump", "Screw Pump",
     "Horizontal Pump", "Sump Pump", "Vaccum Pump", "Cavity Pump",
     "Progressive Cavity Pump", "Positive Displacement", "Liquid Ring Vacuum",
     "Turbine Pump", "Vane Pump", "Ejector")
_add("compressor",
     "Centrifugal Compressor", "Rotary Compressor", "Axial Compressor",
     "Axical COMP", "Reciprocation  Compressor", "Compressor Turbine",
     "Centrifugal Blower")
_add("turbine", "Motor Driven Turbine", "Double Flow Turbine", "Turbine Driver")
_add("fan", "Fan Blades", "Triple Fan Blade", "Counterflow Forced Draft",
     "Counterflow Natural Draft", "Crossflow Inducted")

# --- static equipment ---
_add("vessel", "Tank", "Drum", "Horizontal Vessel", "Vertical Vessel",
     "Cylinder", "Manhole")
_add("tower", "Packed Tower", "Plate Tower", "Chimney Tower")
_add("heat_exchanger", "Heat Exchanger", "Tubular")
_add("fired_heater", "Furnace", "bolier", "Oil Burner", "Automatic Stoker",
     "reformer")
_add("reactor", "Mixing Reactor", "Fluidzed Recactor", "Fluid Catalytic Cracking",
     "Hydrocracking", "Hydrodesulferization", "Alkyalation", "Fluid Cooking",
     "Mixer")
_add("conveyor", "Conveyor", "Screw Conveyor", "Scraper Conveyor",
     "Overhead Conveyor", "Elevator", "Hoist", "Skip Hoist", "Boom Loader")

# --- instruments, grouped by measured variable ---
_add("pressure_instrument",
     "Pressure Gauge", "Pressure Indicator", "Pressure Transmitter",
     "Pressure Controller", "Pressure Recorder", "Pressure Indicating Controller",
     "Pressure Recording Controller", "Gauge")
_add("level_instrument",
     "Level Indicator", "Level Transmitter", "Level Trasmitter",
     "Level Controller", "Level Recorder", "Level Meter", "Level Alarm",
     "Level Alarm High", "Level Alarm Low")
_add("flow_instrument",
     "Flowmeter", "Flow Controller", "Flow Transmitter", "Flow Recorder",
     "Rotameter", "Rotary Meter", "Odometer", "Mangnetic", "Orifice")
_add("temp_instrument",
     "Temp Ind", "Temp Indicator", "temp controller", "temp recorder",
     "temp trasmitter")
_add("misc_instrument", "Sampler", "Not Gate", "radio link")

# --- pipe fittings / connection styles ---
_add("pipe_fitting", "Flanged", "Flanged Ends", "Socket Ends", "Socket Weld",
     "Treaded", "Welded")


# ---------------------------------------------------------------------------


def _load(split_dir: Path) -> tuple[dict, dict]:
    data = json.loads((split_dir / "_annotations.coco.json").read_text())
    names = {c["id"]: c["name"] for c in data["categories"]}
    return data, names


def survey(src: Path) -> tuple[collections.Counter, list[str]]:
    """Count merged classes so we can drop what is still too rare."""
    merged = collections.Counter()
    unmapped: list[str] = []
    for sp in ("train", "valid", "test"):
        d = src / sp
        if not (d / "_annotations.coco.json").exists():
            continue
        data, names = _load(d)
        for a in data["annotations"]:
            raw = names[a["category_id"]]
            if raw in DROP:
                continue
            tgt = MERGE.get(raw)
            if tgt is None:
                unmapped.append(raw)
                continue
            merged[tgt] += 1
    return merged, sorted(set(unmapped))


def convert(src: Path, out: Path = OUT, min_examples: int = MIN_EXAMPLES) -> dict:
    merged, unmapped = survey(src)
    if unmapped:
        print(f"  !! {len(unmapped)} raw classes have no mapping: {unmapped[:8]}")

    keep = sorted(c for c, n in merged.items() if n >= min_examples)
    dropped = {c: n for c, n in merged.items() if n < min_examples}
    idx = {c: i for i, c in enumerate(keep)}

    if out.exists():
        shutil.rmtree(out)
    stats = collections.Counter()
    for sp in ("train", "valid", "test"):
        d = src / sp
        if not (d / "_annotations.coco.json").exists():
            continue
        data, names = _load(d)
        img_dir = out / sp / "images"
        lbl_dir = out / sp / "labels"
        img_dir.mkdir(parents=True, exist_ok=True)
        lbl_dir.mkdir(parents=True, exist_ok=True)

        by_img: dict[int, list] = collections.defaultdict(list)
        for a in data["annotations"]:
            raw = names[a["category_id"]]
            tgt = MERGE.get(raw)
            if raw in DROP or tgt is None or tgt not in idx:
                continue
            by_img[a["image_id"]].append((idx[tgt], a["bbox"]))

        for im in data["images"]:
            boxes = by_img.get(im["id"])
            if not boxes:                      # no usable label: skip the image
                continue
            srcf = d / im["file_name"]
            if not srcf.exists():
                continue
            shutil.copy2(srcf, img_dir / im["file_name"])
            W, H = im["width"], im["height"]
            lines = []
            for cid, (x, y, w, h) in boxes:
                # COCO xywh (top-left) -> YOLO normalised cx cy w h
                cx, cy = (x + w / 2) / W, (y + h / 2) / H
                lines.append(f"{cid} {cx:.6f} {cy:.6f} {w/W:.6f} {h/H:.6f}")
                stats[sp] += 1
            (lbl_dir / (Path(im["file_name"]).stem + ".txt")).write_text(
                "\n".join(lines))

    yaml = ["# Generated by tools/pid_dataset.py - do not edit by hand.",
            f"path: {out.resolve()}",
            "train: train/images",
            "val: valid/images",
            "test: test/images",
            "", "names:"]
    yaml += [f"  {i}: {c}" for i, c in enumerate(keep)]
    (out / "data.yaml").write_text("\n".join(yaml) + "\n")

    return {"classes": keep, "counts": merged, "dropped": dropped,
            "written": dict(stats), "yaml": out / "data.yaml"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Roboflow COCO export folder")
    ap.add_argument("--min", type=int, default=MIN_EXAMPLES)
    ap.add_argument("--no-resplit", action="store_true",
                    help="keep Roboflow's split (9 classes never validated)")
    a = ap.parse_args()

    res = convert(Path(a.src).expanduser(), min_examples=a.min)
    print(f"\n{len(res['classes'])} classes kept (>= {a.min} examples):\n")
    for c in res["classes"]:
        print(f"  {res['counts'][c]:6}  {c}")
    if res["dropped"]:
        print(f"\ndropped as too rare: {res['dropped']}")
    if not a.no_resplit:
        rs = resplit()
        print(f"\nre-split: moved {rs['moved']} images, "
              f"validation now {rs['val_images']} images")
    coco = write_coco()
    print(f"\nCOCO written (for RF-DETR): {coco}")
    print(f"\nboxes written: {res['written']}")
    print(f"dataset yaml : {res['yaml']}")
    return 0




# ---------------------------------------------------------------------------
# re-split
# ---------------------------------------------------------------------------

def resplit(out: Path = OUT, val_frac: float = 0.15, min_per_class: int = 8,
            seed: int = 7) -> dict:
    """
    Rebuild train/valid so every class is actually validated.

    Roboflow's own split puts 2610 images in train and 95 in valid, and nine of
    our 27 classes - compressor, conveyor, fan, fired_heater, misc_instrument,
    reactor, tower, turbine, vessel - appear in valid ZERO times. Training
    against that reports a confident mAP computed over 18 classes while telling
    you nothing about the other nine.

    Greedy stratified assignment: walk images rarest-class-first and send one to
    validation until every class has either its share or `min_per_class` boxes
    there. Rare classes get covered first, common ones are covered anyway.
    """
    import random

    labels: dict[Path, collections.Counter] = {}
    for sp in ("train", "valid"):
        for lf in (out / sp / "labels").iterdir():
            c = collections.Counter()
            for line in lf.read_text().splitlines():
                if line.strip():
                    c[int(line.split()[0])] += 1
            labels[lf] = c

    total = collections.Counter()
    for c in labels.values():
        total.update(c)

    # Two bounds, and the cap matters more than the target. A pure "fill until
    # every class is covered" rule pulled EVERY instance of the rare classes
    # into validation - conveyor, fired_heater, reactor and tower vanished from
    # train entirely, and a class absent from training cannot be learned at all.
    # So: aim for val_frac, never take more than CAP of any class.
    CAP = 0.35
    target = {k: min(max(min_per_class, int(v * val_frac)), max(1, int(v * CAP)))
              for k, v in total.items()}
    cap = {k: max(1, int(v * CAP)) for k, v in total.items()}

    # rarest class an image contains decides its priority
    rank = {p: min(total[k] for k in c) for p, c in labels.items() if c}
    order = sorted(rank, key=lambda p: rank[p])
    tail = order[len(order) // 3:]
    random.Random(seed).shuffle(tail)          # shuffling a slice copies it,
    order = order[:len(order) // 3] + tail     # so stitch it back explicitly

    have: collections.Counter = collections.Counter()
    val_set: set[Path] = set()
    for p in order:
        c = labels[p]
        helps = any(have[k] < target[k] for k in c)
        overflows = any(have[k] + c[k] > cap[k] for k in c)
        if helps and not overflows:
            val_set.add(p)
            have.update(c)

    moved = 0
    for lf, c in labels.items():
        want = "valid" if lf in val_set else "train"
        if lf.parent.parent.name == want:
            continue
        stem = lf.stem
        for sub, ext in (("labels", ".txt"), ("images", None)):
            srcd = lf.parent.parent / sub
            dstd = out / want / sub
            dstd.mkdir(parents=True, exist_ok=True)
            for f in srcd.glob(stem + ".*"):
                shutil.move(str(f), dstd / f.name)
        moved += 1

    return {"moved": moved, "val_images": len(val_set),
            "val_boxes": dict(have), "targets": target}


# ---------------------------------------------------------------------------
# COCO export
# ---------------------------------------------------------------------------

def write_coco(out: Path = OUT) -> dict:
    """
    Emit the merged dataset in COCO form as well.

    Ultralytics wants YOLO txt; RF-DETR wants COCO json. Training both on the
    SAME splits is the only way a comparison means anything - different splits
    and you are comparing luck, not architectures. So this derives COCO from
    the YOLO labels already on disk rather than re-deriving from the raw export.
    """
    import yaml
    from PIL import Image

    names = yaml.safe_load((out / "data.yaml").read_text())["names"]
    written = {}
    for sp in ("train", "valid", "test"):
        img_dir, lbl_dir = out / sp / "images", out / sp / "labels"
        if not img_dir.exists():
            continue
        images, anns = [], []
        ann_id = 1
        for i, imf in enumerate(sorted(img_dir.iterdir()), 1):
            if imf.name.startswith("."):
                continue
            with Image.open(imf) as im:
                W, H = im.size
            images.append({"id": i, "file_name": imf.name, "width": W, "height": H})
            lf = lbl_dir / (imf.stem + ".txt")
            if not lf.exists():
                continue
            for line in lf.read_text().splitlines():
                if not line.strip():
                    continue
                c, cx, cy, w, h = line.split()
                cx, cy, w, h = float(cx), float(cy), float(w), float(h)
                # YOLO normalised centre -> COCO absolute top-left xywh
                x, y = (cx - w / 2) * W, (cy - h / 2) * H
                bw, bh = w * W, h * H
                anns.append({"id": ann_id, "image_id": i,
                             "category_id": int(c) + 1,     # COCO ids start at 1
                             "bbox": [round(x, 2), round(y, 2),
                                      round(bw, 2), round(bh, 2)],
                             "area": round(bw * bh, 2), "iscrowd": 0})
                ann_id += 1
        coco = {"images": images, "annotations": anns,
                "categories": [{"id": i + 1, "name": n, "supercategory": "pid"}
                               for i, n in names.items()]}
        (out / sp / "_annotations.coco.json").write_text(json.dumps(coco))
        written[sp] = (len(images), len(anns))
    return written


if __name__ == "__main__":
    raise SystemExit(main())
