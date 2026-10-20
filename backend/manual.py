"""A vehicle's own field manual, as the jeep's (jeep_manual.yml): an item left beside it when its crate
opens, and a cmuVehicleManual prototype that draws the vehicle from its own sprites and picks each
part out by the click mask the game uses for it, with a page on what the part does, how it breaks and
how to fix and change it.

The pages are the jeep manual's (its jeep-manual.ftl, in the repo the export reads), with the jeep's
name and figures swapped for the vehicle's; pages about parts it hasn't got are left out, and parts
the jeep hasn't got get pages of their own.
"""

import json
import os
import re

JEEP_FTL = ("Content.CMU", "Resources", "Locale", "en-US", "CMU14", "vehicle", "jeep-manual.ftl")

# Kit the jeep carries, by the kit slot's preset id: its manual page.
KIT_PAGES = {"spare": "spare", "jerrycan": "jerrycan", "shovel": "shovel", "axe": "axe", "jack": "jack",
             "extinguisher": "extinguisher"}
# Pages written for this vehicle where the jeep's say where things hang on the jeep.
OWN_PAGES = {
    "door-about": "Shut, it stops shots at the legs of whoever sits beside it, from that side.",
    "lid-about": "A lid over the back. Open it to get at what's stowed behind it.",
    "kit-about": "Kit stowed on the {name}, in a place of its own.",
    "spare-about": "A whole wheel set carried on the {name}. It keeps its own paint and rims.",
    "jerrycan-about": "Fuel carried on the {name}, for the fuel door.",
    "shovel-about": "An entrenching tool, stowed on the {name}.",
    "axe-about": "A fire axe, stowed on the {name}.",
    "jack-about": "It opens vehicle maintenance, where the wheels come off.",
    "extinguisher-about": "Spray it on a burning {name} to put the engine fire out.",
    "seat-about": "A seat for a passenger. Any gun can be fired from it, less steadily the faster the {name} goes.",
    "searchlight-about": "A round searchlight on a hinged yoke. It throws a long, narrow beam wherever it points.",
    "hull-about": "The {name}'s hull is its wheel set. A ruined hull leaves it sagging, unable to drive until it's repaired.",
}


def read_ftl(path):
    """A Fluent file's messages: {key: value}, a multi-line value with its lines' indent kept."""
    out = {}
    key = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            m = re.match(r"^([a-z0-9-]+) =(.*)$", line)
            if m:
                key = m.group(1)
                out[key] = m.group(2).strip()
            elif key and line.startswith(" ") and line.strip():
                out[key] += ("\n" if out[key] else "") + line.rstrip()
            elif not line.strip() or line.startswith("#"):
                key = None
    return out


def ours(text, name, stats):
    """The jeep's text made the vehicle's: its name, and its fuel tank's figures."""
    text = text.replace("M-Jeep", name.title()).replace("jeep's", f"{name}'s").replace("Jeep's", f"{name.capitalize()}'s")
    text = re.sub(r"\bjeeps\b", f"{name}s", text)
    text = re.sub(r"\bjeep\b", name, text)
    text = re.sub(r"\bJeep\b", name.capitalize(), text)
    text = text.replace("holds 60 units of welding fuel and comes 45 full",
                        f"holds {stats['fuel']:g} units of welding fuel and comes {stats['fuelStart']:g} full")
    return text


def build(repo, name, ident, loc, tex, slug, states, parts, stats):
    """The manual's prototypes (YAML) and strings ({key: value}).

    parts: what the vehicle has, as export_game knows it:
        panels   [(pid, kind, layer name)]      hinged panels, by kind (door, hood, tailgate, ...)
        fuel     [pid]                          fuel doors
        engines  [pid]
        shields  [pid]                          windshields
        seats    [(id, driver)]
        kits     [(pid, preset or None, name)]
        lamps    {"head": [pid], "signal": [pid], "tail": [pid], "search": [pid]}
    """
    jeep = read_ftl(os.path.join(repo, *JEEP_FTL))
    strings = {}

    def key(suffix):
        return f"cmu-{loc}-manual-{suffix}"

    def page(suffix, own=None):
        """A page's string: the vehicle's own, or the jeep's of that name made the vehicle's."""
        k = key(suffix)
        if own is not None:
            strings[k] = own.format(name=name)
        elif f"cmu-jeep-manual-{suffix}" in jeep:
            strings[k] = ours(jeep[f"cmu-jeep-manual-{suffix}"], name, stats)
        else:
            return None
        return k

    have = set(states)
    entries = []

    def entry(eid, title, masks=(), open_masks=(), highlight=False, **texts):
        masks = [m for m in masks if m in have]
        open_masks = [m for m in open_masks if m in have]
        out = f"  - id: {eid}\n    name: {title}\n"
        if masks:
            out += f"    masks: [ {', '.join(masks)} ]\n"
        if open_masks:
            out += f"    openMasks: [ {', '.join(open_masks)} ]\n"
        if highlight:
            out += "    highlight: true\n"
        for field in ("about", "use", "damage", "repair", "replace"):
            if texts.get(field):
                out += f"    {field}: {texts[field]}\n"
        entries.append(out)

    def titled(eid, text):
        strings[key(f"part-{eid.replace('_', '-')}")] = text
        return key(f"part-{eid.replace('_', '-')}")

    panel_damage, panel_repair, panel_replace = page("panel-damage"), page("panel-repair"), page("panel-replace")
    weld = page("weld-repair")
    for pid in parts["fuel"]:
        entry(pid, page("fuel-door"), [f"click_{pid}_closed"], [f"click_{pid}_open"], True,
              about=page("fuel-door-about"), use=page("fuel-door-use"), replace=page("fuel-door-replace"))
    for pid, kind, label in parts["panels"]:
        common = dict(damage=panel_damage, repair=panel_repair, replace=panel_replace)
        if kind == "hood":
            texts = dict(about=page("hood-about"), use=page("hood-use"), **common)
        elif kind == "door":
            texts = dict(about=page("door-about", OWN_PAGES["door-about"]), use=page("door-use"), **common)
        elif kind == "tailgate":
            texts = dict(about=page("lid-about", OWN_PAGES["lid-about"]), use=page("tailgate-use"), **common)
        else:
            texts = dict(about=page("lid-about", OWN_PAGES["lid-about"]), **common)
        entry(pid, titled(pid, label.capitalize()), [f"click_{pid}_closed"], [f"click_{pid}_open"], **texts)
    for pid in parts["engines"]:
        entry(pid, page("engine"), [], [f"click_{pid}"], about=page("engine-about"), use=page("engine-use"),
              damage=page("engine-damage"), repair=page("engine-repair"), replace=page("engine-replace"))
    for pid in parts["shields"]:
        entry(pid, page("windshield"), [f"click_{pid}_up"], about=page("windshield-about"),
              use=page("windshield-use"), damage=page("windshield-damage"), repair=weld, replace=panel_replace)
    for sid, driver in parts["seats"]:
        about = page("seat-driver-about") if driver else page("seat-about", OWN_PAGES["seat-about"])
        title = page("seat-driver") if driver else titled(f"seat_{sid}", f"{sid.replace('_', ' ').capitalize()} seat")
        entry(f"seat_{sid}", title, [f"click_seat_{sid}"], [f"click_seat_{sid}_open"], about=about, use=page("seat-use"))
    entry("wheels", page("wheels"), ["click_wheels"], about=page("wheels-about"), use=page("wheels-use"),
          damage=page("wheels-damage"), replace=page("wheels-replace"))
    for pid, preset, label in parts["kits"]:
        jeep_page = KIT_PAGES.get(preset)
        title = page(jeep_page) if jeep_page else titled(pid, label.capitalize())
        about = page(f"{pid}-about", OWN_PAGES.get(f"{jeep_page}-about", OWN_PAGES["kit-about"]))
        entry(pid, title, [f"click_{pid}"], [f"click_{pid}"], about=about, use=page("kit-use"),
              damage=page(f"{jeep_page}-damage") if jeep_page else None)
    lamp_damage, lamp_replace = page("lamp-damage"), page("lamp-replace")
    for role, page_id in (("head", "headlights"), ("signal", "turn-signals"), ("tail", "tail-lights")):
        if parts["lamps"].get(role):
            entry(page_id.replace("-", "_"), page(page_id), [f"click_{pid}" for pid in parts["lamps"][role]],
                  about=page(f"{page_id}-about"), use=page(f"{page_id}-use"), damage=lamp_damage, repair=weld,
                  replace=lamp_replace)
    if parts["lamps"].get("search"):
        entry("searchlight", page("searchlight"), [f"click_{pid}" for pid in parts["lamps"]["search"]],
              about=page("searchlight-about", OWN_PAGES["searchlight-about"]), use=page("searchlight-use"),
              damage=lamp_damage, repair=weld, replace=page("searchlight-replace"))
    for page_id, fields in (("key", ("about", "use")), ("controls", ("about", "use")),
                            ("hull", ("about", "damage", "repair")), ("fire", ("about", "use")),
                            ("crashes", ("about", "damage", "repair")), ("paint", ("about", "use")),
                            ("crate", ("about", "use"))):
        texts = {}
        for f in fields:
            own = OWN_PAGES.get(f"{page_id}-{f}")
            texts[f] = page(f"{page_id}-{f}", own)
        entry(page_id, page(page_id), **texts)

    strings[key("title")] = f"{name.title()} field manual"

    def listed(names):
        return "".join(f"  - {n}\n" for n in names if n in have)

    under = ["base", "wheels_0"] + [f"{p}_0" for p in parts["engines"]]
    under += [f"{p}_closed" for p in parts["fuel"]] + [f"{p}_closed" for p, _, _ in parts["panels"]]
    under += [f"{p}_0" for p, _, _ in parts["kits"]] + [f"{p}_0" for ps in parts["lamps"].values() for p in ps]
    under += [f"{p}_up_0" for p in parts["shields"]]
    over = ["overlay", "wheels_overlay_0"] + [f"{p}_closed_overlay" for p in parts["fuel"]]
    over += [f"{p}_closed_overlay" for p, _, _ in parts["panels"]]
    over += [f"{p}_overlay_0" for p, _, _ in parts["kits"]] + [f"{p}_overlay_0" for ps in parts["lamps"].values() for p in ps]
    over += [f"{p}_up_overlay_0" for p in parts["shields"]]
    opened = {}
    for p in parts["fuel"] + [p for p, _, _ in parts["panels"]]:
        opened[f"{p}_closed"] = f"{p}_open"
        opened[f"{p}_closed_overlay"] = f"{p}_open_overlay"
    for p, _, _ in parts["kits"]:
        opened[f"{p}_0"] = f"{p}_open_0"
        opened[f"{p}_overlay_0"] = f"{p}_open_overlay_0"
    open_yaml = "".join(f"    {a}: {b}\n" for a, b in opened.items() if a in have and b in have)

    yml = f"""
# Its field manual: left beside it when its crate opens.
- type: entity
  parent: CMUJeepManual
  id: CMU{ident}Manual
  name: {json.dumps(name + " field manual")}
  description: {json.dumps("The field manual for the " + name + ", packed in its crate. Every part, what it does and how to fix it.")}
  components:
  - type: CMUVehicleManual
    manual: CMU{ident}Manual

- type: cmuVehicleManual
  id: CMU{ident}Manual
  title: {key('title')}
  sprite: {tex}/{slug}.rsi
  layers:
{listed(under)}  overlayLayers:
{listed(over)}  overlayLift:
    South: 0
    North: 0
    East: 0
    West: 0
""" + (f"  openStates:\n{open_yaml}" if open_yaml else "") + "  entries:\n" + "".join(entries)
    return yml, strings
