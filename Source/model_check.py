"""Check any WWE 2K26 character model: a base, a donor, or a finished swap.

Usage: blender --background --factory-startup --disable-autoexec
  --python-exit-code 1 --python model_check.py -- job.json

job.json: {"character", "attire", "addon_dir", "helpers_dir", "result_path",
           optional "swap_report"}. Nothing is written except the result file.
Each check is {"level": ok|info|warn|problem, "text": plain-language finding}.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import struct
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))

FACIAL_HAIR = re.compile(r'^[MS]_(?:Combinations?|Beard|Facial_?Hair|Mo?ustache|Goatee|Sideburns?)(?:$|[_0-9])', re.I)


def progress(step):
    print('CHECK_PROGRESS ' + json.dumps({'step': step}), flush=True)


def _region(point):
    if point.z > 150:
        return 'neck/head'
    if abs(point.x) > 25 and point.z > 105:
        return 'arms/hands'
    if point.z >= 95:
        return 'torso'
    return 'legs'


def _regions(points):
    counts = {}
    for point in points:
        key = _region(point)
        counts[key] = counts.get(key, 0) + 1
    return ', '.join('%s %d' % item for item in sorted(counts.items(), key=lambda item: -item[1]))


def _tree(objects):
    from mathutils.bvhtree import BVHTree
    verts, tris = [], []
    for obj in objects:
        offset = len(verts)
        verts.extend(obj.matrix_world @ v.co for v in obj.data.vertices)
        obj.data.calc_loop_triangles()
        tris.extend(tuple(offset + i for i in t.vertices) for t in obj.data.loop_triangles)
    return BVHTree.FromPolygons(verts, tris, all_triangles=True) if tris else None


def run(job):
    import bpy
    sys.path.insert(0, str(Path(job['helpers_dir'])))
    sys.path.insert(0, str(Path(job['addon_dir']).parent))
    import WWE2K_MDL
    WWE2K_MDL.register()
    import swap_worker as sw
    from model_layout import resolve_attire, has_base, named_child, one_model, model_header

    checks = []

    def add(level, text):
        checks.append({'level': level, 'text': text})

    root, attire = resolve_attire(job['character'], job.get('attire'), require_base=False)
    progress('Reading ' + root.name + ' / ' + attire.parent.name)
    header = model_header(attire)
    if header['model'] == 47:
        add('ok', 'Attire is a WWE 2K26 model (%s version 47).' % header['format'])
    else:
        add('problem', 'Attire is model version %d; swaps need 2K26 version 47. Port it with Model Formatter first.'
            % header['model'])

    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    base = None
    if has_base(root):
        base = sw._import_model(one_model(named_child(root, 'BaseModel'), base=True), 'base_model')
        counts = sw._body_counts(base['meshes'])
        if set(counts) == {'m_body', 'm_head'}:
            add('ok', 'BaseModel has a body (%d points) and a head (%d points).' % (counts['m_body'], counts['m_head']))
        else:
            add('problem', 'BaseModel is missing M_Body or M_Head; it cannot be used as a swap base.')
    else:
        add('info', 'No BaseModel folder: fine as an attire donor, but it cannot be a swap base. Body checks skipped.')

    progress('Reading the attire meshes')
    model = sw._import_model(attire, 'attire')
    hair, clothing = [], []
    for obj in model['meshes']:
        try:
            kind = sw._classify_mesh(obj)
        except ValueError as error:
            add('problem', str(error))
            continue
        (hair if kind == 'hair' else clothing).append(obj)
    name = lambda obj: sw.clean_mesh(obj.data.name)
    listing = lambda objs: ', '.join(sorted({name(o) for o in objs})) or 'none'
    facial = [o for o in hair if FACIAL_HAIR.match(name(o))]
    head_hair = [o for o in hair if o not in facial]
    if head_hair:
        add('ok', 'Hair: ' + listing(head_hair) + '.')
    else:
        add('warn', 'No hair meshes found. As a base, the swap needs the base hair to keep.')
    if facial:
        add('info', 'Facial hair (beard): ' + listing(facial)
            + '. A swap keeps the base\'s beard and leaves a donor\'s beard out.')
    visible_clothing = [o for o in clothing if not name(o).startswith('S_')]
    add('ok' if visible_clothing else 'warn', 'Clothing: ' + listing(visible_clothing) + '.')
    if 'J_Hair' in model['rig'].data.bones:
        add('ok', 'Hair bones (J_Hair) present.')
    else:
        add('info', 'No hair bones (J_Hair). That is fine: the hair is kept exactly as it is.')

    # Hair materials must link their textures, or hair is invisible in game.
    progress('Checking hair materials')
    folder = attire.parent
    textures = next((p for p in folder.iterdir() if p.is_dir() and p.name.casefold() == 'textures'), None)
    materials = next((p for p in folder.iterdir() if p.is_dir() and p.name.casefold() == 'materials'), None)
    prefix = 'Characters/%s/%s/Textures/' % (root.name, folder.name)
    local = {sw._fnv(prefix + p.stem + '.tex'): p.name for p in textures.iterdir() if p.is_file()} if textures else {}
    from WWE2K_MDL.Blender.Extras.jmtl_resolver import extract_full_material_metadata
    from merge_helpers import clean_material_name
    broken, linked = [], []
    for obj in hair:
        for slot in obj.material_slots:
            if slot.material is None or not materials:
                continue
            stem = clean_material_name(slot.material.name)
            files = [p for p in materials.glob('*.jmtl') if p.stem.casefold() == stem.casefold()]
            if len(files) != 1:
                continue
            data = files[0].read_bytes()
            meta = extract_full_material_metadata(data) or {}
            if meta.get('shader_byte') != 0x16:
                continue
            refs = {local[v] for v in (struct.unpack_from('<Q', data, i)[0] for i in range(len(data) - 7)) if v in local}
            expected = [n for n in local.values() if n.casefold().startswith(stem.casefold().replace('_alpha', '') + '_')]
            if expected and not refs:
                broken.append('%s (should link %s)' % (stem, ', '.join(sorted(expected))))
            elif refs:
                linked.append(stem)
    if broken:
        add('problem', 'Hair material(s) with no texture links: ' + '; '.join(broken)
            + '. The hair may be missing or see-through in game.')
    elif linked:
        add('ok', 'Hair materials link their textures: ' + ', '.join(sorted(set(linked))) + '.')

    # YCL physics: which cages it drives, and whether they exist.
    progress('Checking physics (.ycl)')
    ycl = [p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() == '.ycl'
           and p.stem.casefold() == attire.stem.casefold()]
    cages = {name(o) for o in model['meshes'] if name(o).startswith('S_')}
    if ycl:
        named = {m.decode() for m in re.findall(rb'S_[A-Za-z0-9_]+', ycl[0].read_bytes())}
        add('ok', 'Physics file %s drives: %s.' % (ycl[0].name, ', '.join(sorted(named)) or 'nothing named'))
        absent = sorted(named - cages)
        if absent:
            add('info', 'The .ycl also lists cage(s) not in this model: ' + ', '.join(absent)
                + '. Usually harmless (entrance-only or removed gear); check physics in game.')
        unused = sorted(cages - named)
        if unused:
            add('warn', 'Physics cage(s) with no .ycl entry: ' + ', '.join(unused) + '. That hair or cloth will not move.')
    elif cages:
        add('warn', 'No .ycl physics file, but the model has physics cages (' + ', '.join(sorted(cages))
            + '). That hair or cloth will not move in game.')
    else:
        add('info', 'No .ycl physics file and no physics cages: everything is static.')

    if base is not None:
        _check_body(sw, attire, base, clothing, add)

    report = job.get('swap_report')
    if report and Path(report).is_file():
        _check_swap_report(json.loads(Path(report).read_text(encoding='utf-8')), add)

    problems = sum(1 for c in checks if c['level'] == 'problem')
    warnings = sum(1 for c in checks if c['level'] == 'warn')
    return {'character': str(root), 'attire': folder.name, 'checks': checks,
            'summary': {'problems': problems, 'warnings': warnings}}


def _check_body(sw, attire, base, clothing, add):
    """Skin showing through clothing, and hide-masks that open holes."""
    import math
    progress('Checking skin under the clothing')
    from WWE2K_MDL.Serialize.hpl_roundtrip import extract_hpl_from_mcd, apply_hpl_to_basemodel
    for obj in base['meshes']:
        for group in list(obj.vertex_groups):
            if group.name.startswith('HPL_'):
                obj.vertex_groups.remove(group)
    hpl = extract_hpl_from_mcd(str(attire))
    if hpl and len(hpl) > 32:
        ok, message = apply_hpl_to_basemodel(hpl)
        if not ok:
            add('warn', 'Body hide-masks (HPL) could not be read: ' + message)
    else:
        add('warn', 'No body hide-masks (HPL). Skin may show through tight clothing.')
    bodies = [o for o in base['meshes'] if sw.clean_mesh(o.data.name).casefold() in ('m_body', 'm_head')]
    occluder = _tree(bodies)
    body = next((o for o in bodies if sw.clean_mesh(o.data.name).casefold() == 'm_body'), None)
    if body is None:
        return
    mesh = body.data
    normal_matrix = body.matrix_world.to_3x3().inverted().transposed()
    points = [body.matrix_world @ v.co for v in mesh.vertices]
    normals = [(normal_matrix @ v.normal).normalized() for v in mesh.vertices]
    for mode in ('Ring', 'Entrance'):
        wanted = [o for o in clothing if not sw.clean_mesh(o.data.name).startswith('S_') and o.data.polygons
                  and sw._clothing_visibility(o)[0 if mode == 'Ring' else 1]]
        tree = _tree(wanted)
        if tree is None:
            continue
        group = body.vertex_groups.get('HPL_' + mode)
        masked = {v.index for v in mesh.vertices
                  if group is not None and any(g.group == group.index for g in v.groups)}
        through, holes = [], []
        for i, (p, n) in enumerate(zip(points, normals)):
            if n.length < .5:
                continue
            # Same rule as the swap: fabric just behind the skin, inside the body.
            behind = sw._skin_poking_out(tree, occluder, p, n)
            if i not in masked:
                if behind:
                    through.append(p)
                continue
            if behind:
                continue
            tangent = n.orthogonal().normalized()
            other = n.cross(tangent)
            start = p + n * .05
            for direction in sw._view_directions(n, tangent, other):
                if tree.ray_cast(start, direction, 60.0)[0] is None and occluder.ray_cast(start, direction, 60.0)[0] is None:
                    holes.append(p)
                    break
        label = 'ring gear' if mode == 'Ring' else 'entrance gear'
        if through:
            level = 'problem' if len(through) > 150 else 'warn'
            add(level, 'Skin showing through the %s at %d point(s): %s.' % (label, len(through), _regions(through)))
        else:
            add('ok', 'No skin showing through the %s.' % label)
        if holes:
            level = 'problem' if len(holes) > 25 else 'warn'
            add(level, 'Hide-mask holes in %s (hidden skin you can see into) at %d point(s): %s.'
                % (label, len(holes), _regions(holes)))
        elif masked:
            add('ok', 'Hide-masks for %s open no holes.' % label)


def _check_swap_report(report, add):
    """Extra checks for a finished swap: the base must be untouched."""
    preserved = report.get('base_model_preservation', {})
    if preserved.get('byte_identical'):
        add('ok', 'BaseModel files are byte-for-byte the base\'s.')
    elif preserved:
        add('info', 'BaseModel copied with an approved fix: ' + ', '.join(preserved.get('allowed_changed_paths', [])))
    changes = [h.get('max_world_change', 0) for h in report.get('hair_geometry', [])]
    if changes:
        add('ok' if max(changes) <= 1e-5 else 'problem',
            'Base hair geometry unchanged.' if max(changes) <= 1e-5 else 'Base hair geometry changed.')
    restored = report.get('restored_base_hair_materials')
    if restored is not None:
        add('ok', 'Base hair material files shipped unchanged: %d.' % len(restored))
    excluded = report.get('selection', {}).get('excluded_donor_hair', [])
    if excluded:
        add('ok', 'Donor hair/beard left out: ' + ', '.join(sorted({re.sub(r'\.\d+$', '', n) for n in excluded})) + '.')
    for item in report.get('fit', {}).get('objects', []):
        if item.get('status') == 'unchanged_unsafe_fit':
            add('warn', 'Clothing %s was not fitted: %s' % (item.get('mesh'), item.get('reason')))


def main():
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    job = json.loads(Path(args[0]).read_text(encoding='utf-8-sig'))
    result = {'success': False, 'checks': []}
    try:
        result.update(run(job))
        result['success'] = True
    except BaseException as error:
        result['error'] = str(error)
        result['traceback'] = traceback.format_exc()
    Path(job['result_path']).write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    print('CHECK_RESULT ' + json.dumps({'success': result['success']}), flush=True)
    if not result['success']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
