# CMU Open Vehicles

Draw an open vehicle in orthographic views; see its game sprites live, turning, with the game's hover
outlines and opening doors; download it as a zip that goes straight into the CMU repo. A web app:
the page runs in any browser, and each tab gets its own Python backend on the server, rendering with
the CMU vehicle generator.

## Run it yourself

```
pip install numpy Pillow aiohttp
python tools/snapshot.py C:\path\to\your\CMU\checkout   # the bit of the repo the tool needs, into gamedata/
python backend/web.py                                    # then open http://localhost:8080
```

`gamedata.txt` lists that bit of the repo: the generator, the jeep's sprites, the damage types, the
vehicle sounds and two files that say which game features the repo has. To host it for others, see
**Hosting** below.

Projects are kept in the browser that made them (**Save**, and they save themselves as you work);
**Open** lists them, or opens a `.cmuov` from the computer, and **Download** gives you the `.cmuov`
to keep or share.

## Hosting

On a Debian or Ubuntu server, as root, one line sets it all up (Python, the game data, a service, and
its site on port 80) and run again updates it. It only adds its own site beside what the server
already hosts, through the nginx, Apache or Caddy already on port 80 (its config tested, then
reloaded), or a new Caddy when there's none:

```
curl -fsSL https://raw.githubusercontent.com/Ignies/OpenVehicles-Editor/main/install.sh | sudo bash
```

It serves `openvehicles.ignies.net` by default; `sudo env DOMAIN=example.org bash` in place of
`sudo bash` names another, and `HTTPS=1` has Caddy serve it on 443 with a certificate it gets itself.
The game data is a sparse clone of the CMU repo's jeeps branch (`CMU_REPO`, `CMU_REF`), only the
paths in `gamedata.txt`. Logs: `journalctl -u openvehicles -f`.

## How it works

A vehicle is a stack of **layers**: body, wheels, each door, hood, tailgate, rear hatch or boot lid, windshield,
fuel door, lamps, parts. Each layer is drawn in five views at one pixel per voxel:

| View | Seen from | Columns × rows |
| --- | --- | --- |
| Side | its right, nose right | length × height |
| Left | its left, nose left | length × height |
| Front | ahead | width × height |
| Top | above, nose right | length × width |
| Back | behind, optional | width × height |

A layer on both sides takes its right side from Side and its left from Left. While one of them is
blank it shows the other mirrored (dimmed, "mirrors the right side"), and drawing on it starts from
that mirror, so a detail drawn on one side never cuts the rest away. With **mirror** on, a stroke on
one side lands on the other too; turn it off (M) for a detail on one side only. A layer on one side
is drawn in that side's view; the other is greyed out. (Projects from before the Left view had a
left layer's drawing in Side, nose right: they open with it moved to Left.)

Every pixel has a **material** (the generator's palette) and a **depth**: how far into the box, from
that view's side, the surface sits. A layer is the box carved by every view drawn on it (a voxel
layer, below, is not): nothing through an empty pixel, nothing in front of a surface. Views left
blank don't limit it; a **thickness** keeps a layer that thin behind its surface (a lamp drawn only
from the front). Depth on the top view lowers areas, for an open tub or seats.

Layer **kinds** set how a part opens when clicked in the preview: doors on their front edge, the hood
from its back, a tailgate down on its bottom edge, a rear hatch up on its top edge, a boot lid up
from its front edge, the windshield forward. **Side** puts a layer on the left, the right, or both.
**Rides on** hangs a layer on a hinged one, so it swings with it (kit on a door or tailgate).

A layer loaded from the generator keeps the generator's **model**: what you draw is applied over it,
pixel by pixel along each pixel's line of sight (erasing cuts through, a deeper depth dents in, a
nearer one builds out, and the surface hit takes the material), so everything you don't touch stays
exactly as the game draws it, seat backs and wheel faces included. Each view shows the model from
the direction the game shows that side (Side as it faces east, Left as it faces west, Front south,
Back north), so Left shows the driver's side, seat and steering wheel, to draw on like any other.
(Projects from before this open with their unchanged pixels brought up to it; what was drawn stays.)
**Model: Drawing** lets the layer go, to be carved from its views alone.

A view only holds what's first along each line of sight, so a layer carved from its views can't hold
one thing behind another: a cockpit behind a door, a dash under the windshield, wheel wells, a door's
inner skin. And the faces only the game's slanted directions see (the inside of the far door, the
floor under the dash) can't be coloured. **Model: Voxels** bakes a layer into a **voxel layer**, which
keeps its voxels themselves, each with its own material: a drawn layer as it's carved (each voxel the
colour of the first view that shows it: top, side, left, front, back), one from the generator as its
model faces east, with what was drawn on it. From then on the voxels are its shape (Ctrl+Z undoes the
bake; **Drawing** makes it a drawing of its views again, losing what they can't show). Its views show
it from each side, all five of them (a left door's inside is what the Side view sees), and what you
draw on them works like drawing over the generator's model: along each pixel's line of sight, so a
new pixel builds as deep as the voxels beside it, a deeper depth dents in and a nearer one builds out.
In the Brush card, **Extrude** fills that many voxels in from the pixel's depth, or **Through** to
the far side, instead of a surface (a dash, a seat, a bulkhead in one stroke), and **Erase** cuts
through everything along the line or takes off only the **Surface**. A voxel layer shows a "voxels"
badge in the list.

Each view of a voxel layer can be **sliced** (the slice button on its card, or , and . to step the
active view's): it shows the layer's cross-section that deep from its side, the voxels behind it
faint and those in front hidden, and the other views draw a line where the slice cuts them. Drawing
in a slice sets or clears single voxels in it, so the inside of a body, a wheel well or a door's
inner skin is drawn layer by layer. Clear on a sliced view empties the slice; unsliced, the layer.

**Paint on the preview** (the brush by the preview's controls, or Alt+click) gives the voxel under
the cursor the current material, and with mirror on its mirror image too: the way to colour what only
the slanted sprites show. Hovering names the voxel's f, r, z. A layer still carved from its views
says to make it voxels first.

A layer's **damage states** are drawings of their own: under Layers, **Damage states** › **Add** starts
one as a copy of the layer, a blank one, or another layer's drawing. A state has a name and a damage
type (from the game's prototypes), both editable in its card, and is drawn at **levels**: 20, 40, 60,
80 and 100 of that damage taken, each its own drawing. The part shows the highest level it has
reached. Click a level to draw it in the views (the preview shows it while you do); an empty level
starts as a copy of the one below it. **X** shows each layer's last state at its top level. States
sit under their layer in the list, their levels a click away.

The preview shows the vehicle **driving** the way it faces, its wheels turning on the game's frames
and its body bobbing on the suspension over them (the game's bob, stronger the faster it goes) while
the ground scrolls under it; the thumbnails turn it, Space parks it. It renders with the
generator's own renderer and shading, and the game's paint shader (body, tub and seat colours, dirt).
Hover a part for its outline; click a hinged one to swing it open, again to shut it. The generator
file is watched: edit it and the preview reloads. The open project is watched too: when a script
changes it, the app reloads it (unless it has unsaved changes of its own).

The side panel's tabs: **Layers** (and damage states), **Draw** (materials and brush), **Paint**,
**Vehicle** (wheels, driving, the game; the box's **Shape**: Keep resizes the box around the drawing,
Stretch scales every drawing with it) and **Generate**. Ctrl+1 to 5 switch them.

### Camera

The Vehicle tab's **Camera** card sets how each sprite direction sees the vehicle: **Game's views**
(the generator's, as the jeep is drawn), **Custom** (pitch, yaw, zoom and stretch per direction) or
**One view** (one direction's picture, turned a quarter at a time to point the way each direction
faces; crayons go on what that view shows). A one view vehicle needs the game's crayon
`fixedDirection`; an imported sprite doesn't.

### Importing a finished sprite

**New → Import a sprite…** takes an Aseprite file (or a PNG per layer, named by its file) drawn from
above, and the way its front points in the picture. Its layers by name:

| Layer | What it is |
| --- | --- |
| `base`, `overlay` | The body, under and over the riders |
| `damaged`, `damaged_overlay` | The body worn (else scuffed for you) |
| `wreck` | The burnt out wreck (else the body burnt dark) |
| `wheels` | What shows of the wheels (else hidden wheels under the body) |
| `door_driver`, `door_passenger`, `hood`, `tailgate`, `boot`, `hatch`, `fuel_door` | Panels, each with `_open` for its look opened |
| `headlight_left`, `taillight_right`, `signal_left`, `searchlight`, ... | Lamps |

The picture is used as drawn, turned to point the way each direction faces; the tool presses each layer into a block for
the hull, clicks, seats and crayon map. The board shows the picture alone: pick a seat in the Seats
card and click where its rider sits. **4 dir workspace** on the Camera card shows the blocks.

## Keys

| Key | Does |
| --- | --- |
| B E G L R O D I | pencil, eraser, fill, line, rectangle, ellipse, depth brush, picker (Alt+click picks too) |
| right click | erase |
| [ ] | depth down, up |
| , . | a voxel layer's slice in the active view: nearer, deeper (starts slicing) |
| < > | copy the nearer slice into this one; carry this slice into the next and go there |
| K | onion skin: the slices either side of the one sliced |
| Alt+click the preview | paint the voxel there |
| M | mirror left and right (across each view's middle, and between Side and Left) |
| V | show depth |
| H | show the other layers faintly |
| ↑ ↓ | previous, next layer |
| Space | drive or park the preview |
| X | show each layer's last damage state at its top level |
| Esc | stop drawing a damage level |
| Ctrl+1 … Ctrl+5 | the side tabs |
| Ctrl+Z, Ctrl+Y | undo, redo |
| Ctrl+S, Ctrl+Shift+S, Ctrl+O, Ctrl+N | save, save as, open, new |

Projects save as `.cmuov` (JSON). **Export** writes the four directions as a 2×2 RSI-style sheet and
the voxels as JSON. **Generate art** runs the whole generator and shows its progress.

## Scripts and language models

`backend/cmuov.py` does what the app does from the command line, so a script or a language model can
build a vehicle by editing its views and parameters and look at the result, without touching the
generator. It reads and writes the same `.cmuov` file (an open app reloads it live) and prints JSON.

```
python backend/cmuov.py new truck.cmuov --box 72 44 36          # or --from cargo for a generator jeep
python backend/cmuov.py draw truck.cmuov Body side '[{"op": "polygon", "space": "vehicle",
    "points": [[-30, 5], [30, 5], [30, 18], [-30, 18]], "mat": "od", "depth": 2}]'
python backend/cmuov.py show truck.cmuov Body side --depth      # the view as text, to read back
python backend/cmuov.py layer truck.cmuov add door --name "Driver door"
python backend/cmuov.py layer truck.cmuov set "Driver door" sides=left thickness=1
python backend/cmuov.py stage truck.cmuov "Driver door" add "Bullet holes" --type Piercing --from 20
python backend/cmuov.py stage truck.cmuov "Driver door" add "Bullet holes" --from 40  # its next level
python backend/cmuov.py stage truck.cmuov "Driver door" set "Bullet holes" name=Shot type=Blunt
python backend/cmuov.py reshape truck.cmuov 90 50 40 --stretch
python backend/cmuov.py preview truck.cmuov --out shots --drive E --swing "Driver door" --damage all
python backend/cmuov.py layer truck.cmuov bake Body             # make it a voxel layer
python backend/cmuov.py draw truck.cmuov Body side '[{"op": "box", "from": [-12, -14, 6],
    "to": [0, 13, 18], "mat": "empty"}]'                         # hollow out a cockpit
python backend/cmuov.py show truck.cmuov Body side --slice 8    # its cross-section 8 deep
```

Draw operations: `pixels`, `line`, `rect`, `ellipse`, `polygon`, `fill` (flood), `text` (rows of
characters and a legend, with depths in base 36), `depth` (depth only), `clear`, `shift`, each with
`mat` (a palette name, or `empty`), `depth` or `add`, and `mirror`. Points are view pixels, or with
`"space": "vehicle"` the generator's coordinates: side and left (f, z), front and back (r, z), top
(f, r). Drawing on a blank `left` (or `side`) of a layer on both sides starts from the other side
mirrored, as in the app.
`materials` lists the palette, `types` the damage types, `info` the box and layers;
`python backend/cmuov.py --help` has the rest. Every drawn view cuts the layer along its lines of
sight, so a part's silhouette has to be in every view drawn on it: a cab drawn on the side but not
the front is cut away by the front.

`layer FILE bake LAYER` makes a layer a voxel layer (saved as `voxels`: its grid over the box,
run-length coded, in place of its views). On one, the same operations work along each pixel's line
of sight, as in the app, and take more: `"extrude": [d0, d1]` fills each pixel's line between those
depths instead of drawing a surface, `"extrude": n` fills n voxels in from its depth, `"through":
true` from its depth to the far side, `"slice": d` sets (or with `"mat": "empty"` clears) only the
voxel d deep, `"erase": "surface"` takes off only the first voxel, and `mirror` on side or left
does the same on the other side. Clear empties the layer and shift moves its voxels. Two solids, in
vehicle space whatever the view: `{"op": "box", "from": [f, r, z], "to": [f, r, z], "mat"}` and
`{"op": "cylinder", "axis": "f" | "r" | "z", "center": [the other two], "radius": n, "from": a,
"to": b, "mat"}` (a voxel is in when its centre is, so a centre on a whole number lies between
voxels), `"mat": "empty"` to cut them out, `"mirror": true` to add their reflection across the
centre (r to -r-1). `show` prints a voxel layer's view as what's first along each line;
`--slice D` prints its cross-section D deep instead. With these a script can build what a carved
layer can't hold: hollow a body, set a dash and seats in the cockpit, line the doors' insides.

`examples/roadster/build.py` builds a whole car this way: a civilian roadster after the second
generation MX-5, described in 3D part by part (body, hood, boot lid, doors, fuel door, seats, steering
wheel, folded top, windshield, lamps, wheels), each part's views drawn on its own layer with
`cmuov.py draw`. `python build.py --check` reports how closely the carved layers match. Things it
showed: draw each part in every view it shows in and leave thickness at 0 (a thickness cuts in every
drawn view, so a panel drawn in all five loses most of itself); keep top surfaces in flat planes
(every step in height draws a line); put a wheel's pattern in the rim face's colours (a recess hides
under the edge above it from the slanted views); and a very dark paint turns a vehicle's sides
black, as the game's paint shader takes a fixed amount off faces in shadow.

## Starting, depth and the game

**New** starts blank, from a template (`car`, `pickup`, `van`, `jeep-like`; `backend/templates.py`,
`cmuov.py new FILE --template NAME`): every part laid out as a voxel layer, named for the game, with
its seats placed), from a finished vehicle (the roadster, `examples/`), or from the generator's own
jeeps (cargo, gunner, transport, medical), their model kept with what's drawn applied over it. **Vehicle › Wheels** builds the Wheels layer from axle places, radius, track and tyre
width. **Draw › Trace** puts a picture under a view (a blueprint, a photo) to draw over, and
**Draw › Materials › Add** gives the vehicle a colour of its own beyond the generator's (saved in the
project, unpainted in game). A hinged layer's **Hinge** and **Opens** set the edge it swings on and how
far; a part's **Kit** makes it stowed kit with a slot of its own.

The **Depth** panel beside the Top view shows the active voxel layer in 3D, cut away in front of the
active view's slice, over a ruler of that view's slices (a bar per slice, as full as it is). Click or
drag the ruler to slice there, hover it to see a slice; drag the 3D view to turn and tilt it, click a
voxel in it to slice through that voxel. Slicing a layer carved from its views makes it voxels first.
**K** shows the slices either side of the one drawn (nearer warm, deeper cool), **<** copies the
nearer slice into this one, **>** carries this slice into the next and goes there: a shape built one
layer at a time.

**Vehicle › In the game**: the vehicle's id, name, description and seats (click a seat's place, then
where its rider sits on the views), **Driving** (top speed, hull, fuel, crash damage and every sound,
each playable), a live check of what would stop it going in, then **Download for the game**: a zip
of its sprites, crayon map, prototypes, strings and CMU3D model (`backend/export_3d.py`), laid out
under `CMUOpenVehicles/` as in the repo, with the project and an `INSTALL.md`. Unzip it at the repo's
root. (`cmuov.py game` still writes straight into a checkout, and `backend/gametest.py` can start
the repo's test server from a script.)

## Writing it into the game

`cmuov.py game FILE --repo REPO` (or the app's game export, `export_game.write`) checks the project
first (`export_game.check`, fast, no rendering: a body, wheels, a driver's seat inside the body,
doors that are hinged panels, unique part ids, a valid id, kit on real panels, sane stats and sound
paths) and stops on its errors; its warnings come back with the result. Then it renders every state
the game's open vehicle systems can ask for and writes the RSI, crayon map, prototypes and strings
under `CMUOpenVehicles/Vehicles/<Id>/`.

- **Windshield**: a layer of kind `windshield` folds forward over its bottom edge until it lies on
  the hood or cowl, up, down and folding, whole and shot through. It goes in the `vehicle-windshield`
  slot (`CMUJeepWindshield`), keeps the hood shut while down, and the front row behind it is
  sheltered by it (a seat's `"windshield": true/false` overrides). With a hood, the hood gets a slot
  too, so its raised lid can stand in front of the windshield as the jeep's does.
- **Kit**: a layer with `"kit": {"item", "tag", "host", "maskOnly", "name"}` is stowed kit in a slot
  of its own (`export_game.KIT_PRESETS` has the jeep's spare, jerry can, shovel, axe, jack and
  extinguisher). Hung on a hinged panel (`host`) it swings with it. The first jerry can takes the
  jeep's jerry can slot, so the game can hole it; a spare wheel also gets the road tyre states.
- **Damage states**: each layer's worst level is its broken look in game (the body's is the frame
  damage that fades in as the hull wears; a lamp's, the engine's, the windshield's and kit's broken
  states). Every level is also drawn as `{part}_dmg_{state}_{level}`, and when the repo has
  `CMUVehicleDamageStates` the vehicle shows each at the level its part has reached in that damage
  type.
- **Stats and sounds**: `vehicle.stats` (`maxSpeed`, `acceleration`, `integrity`, `fuel`,
  `fuelStart`, `crashDamage`) override the base's; `vehicle.sounds` (`start`, `idle`, `drive`,
  `rough`, `roughDrive`, `rev`, `stall`, `horn`, `hornLong`, `blinker`, `door`, `doorClose`, `lid`,
  `lidClose`, `windshield`) are resource paths in place of the jeep's.
- **Size**: the game draws open vehicles in 96 px frames; a drawing that runs off one stops the
  export, naming the direction and how far. A body wider than the jeep's gets a wider hull, and the
  result says how wide a corridor it needs.
