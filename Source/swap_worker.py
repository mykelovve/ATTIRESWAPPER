"""WWE 2K26 draft attire merge, executed by an external Windows Blender.

Usage: blender --background --factory-startup --disable-autoexec
  --python-exit-code 1 --python swap_worker.py -- job.json

Source files are never edited. Exported models are visual drafts: the supplied
addon cannot retarget the two sources' external YCL/WDF solver payloads safely.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import traceback
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from swap_job import sha256, validate_job


def progress(fraction, step):
    print('SWAP_PROGRESS ' + json.dumps({'fraction': fraction, 'step': step}), flush=True)


def clean_mesh(name):
    return re.sub(r'\.\d{3,}$', '', name.split('!', 1)[0].split('$', 1)[0])


def _json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.writing')
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')
    os.replace(temporary, path)


def _manifest(directory):
    return {p.relative_to(directory).as_posix(): sha256(p)
            for p in sorted(Path(directory).rglob('*')) if p.is_file()}


def _repair_missing_eyelash(base_directory, character_name, enabled=True):
    """Repair one absent eyelash texture binding on an OUTPUT copy only.

    Populated bindings, other shaders, custom materials, ambiguous paths and
    missing textures are deliberately left alone. No geometry/texture/MTLS is
    touched. The caller still verifies every other copied BaseModel byte.
    """
    if not enabled:
        return []
    from WWE2K_MDL.Blender.Extras.jmtl_resolver import extract_full_material_metadata
    from WWE2K_MDL.Serialize.jmtl_writer import write_jmtl
    base_directory = Path(base_directory)

    def unique(directory, name, is_directory=False):
        matches = [p for p in directory.iterdir() if p.name.casefold() == name.casefold()
                   and (p.is_dir() if is_directory else p.is_file())]
        return matches[0] if len(matches) == 1 else None

    materials = unique(base_directory, 'Materials', True)
    textures = unique(base_directory, 'Textures', True)
    if materials is None or textures is None:
        return []
    material = unique(materials, 'eyelash.jmtl')
    texture = unique(textures, 'eyelash_color.dds')
    if material is None or texture is None:
        return []
    before = material.read_bytes()
    metadata = extract_full_material_metadata(before)
    if not metadata or metadata.get('shader_byte') != 0x14:
        return []
    if metadata.get('color_map_hash') not in (None, 0, 0xFFFFFFFFFFFFFFFF):
        return []
    with texture.open('rb') as handle:
        header = handle.read(128)
    if len(header) != 128 or header[:4] != b'DDS ':
        return []
    texture_path = 'Characters/%s/%s/Textures/%s.tex' % (
        character_name, base_directory.name, texture.stem)
    wanted = _fnv(texture_path)
    after = write_jmtl(0x14, {'colorMap@ref': wanted})
    parsed = extract_full_material_metadata(after)
    if not parsed or parsed.get('shader_byte') != 0x14 or parsed.get('color_map_hash') != wanted:
        raise RuntimeError('Eyelash material repair did not round-trip its texture binding')
    material.write_bytes(after)
    return [{'relative_path': material.relative_to(base_directory).as_posix(),
             'reason': 'Missing eyelash color/alpha texture binding',
             'before_sha256': hashlib.sha256(before).hexdigest(),
             'after_sha256': hashlib.sha256(after).hexdigest(),
             'before_bytes': len(before), 'after_bytes': len(after),
             'texture_path': texture_path, 'texture_ref': wanted,
             'texture_sha256': sha256(texture), 'shader_byte': 0x14}]


def _verify_copied_base(source_base, output_base, original_hashes, repairs):
    """Allow only the explicitly audited missing-eyelash binding exception."""
    from WWE2K_MDL.Blender.Extras.jmtl_resolver import extract_full_material_metadata
    if _manifest(source_base) != original_hashes:
        raise RuntimeError('Source BaseModel files changed during the job')
    expected = dict(original_hashes)
    if len(repairs) > 1:
        raise RuntimeError('Only one output eyelash material repair is allowed')
    for repair in repairs:
        path = repair['relative_path']
        if path.casefold() != 'materials/eyelash.jmtl' or expected.get(path) != repair['before_sha256']:
            raise RuntimeError('Unapproved BaseModel preservation exception')
        metadata = extract_full_material_metadata((Path(output_base) / path).read_bytes())
        if not metadata or metadata.get('shader_byte') != 0x14 or metadata.get('color_map_hash') != repair['texture_ref']:
            raise RuntimeError('Output eyelash binding failed final verification')
        expected[path] = repair['after_sha256']
    actual = _manifest(output_base)
    if actual != expected:
        raise RuntimeError('BaseModel preservation failed outside the allowed eyelash material repair')
    return actual


def _shader(mat):
    value = mat.get('vc_2k26_shader_byte')
    if value is None:
        return None
    return int(value, 0) if isinstance(value, str) else int(value)


def _import_model(path, source):
    import bpy
    before = set(bpy.data.objects)
    result = bpy.ops.import_vcskinmodel.load(
        filepath=str(path), import_shape_keys=True, split_material_groups=False,
        load_textures=True, load_attachment_points=True, load_goto_points=True,
        auto_apply_hpl=False)
    if result != {'FINISHED'}:
        raise RuntimeError('Import failed: %s (%s)' % (path, result))
    objects = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in objects if o.type == 'MESH']
    rigs = [o for o in objects if o.type == 'ARMATURE']
    if not meshes or len(rigs) != 1:
        raise RuntimeError('%s needs exactly one rig and at least one mesh; found %s rigs'
                           % (path, len(rigs)))
    for obj in objects:
        obj['swap_source'] = source
    candidates = [c for c in rigs[0].users_collection if c.get('vcmodel_source_path')]
    if len(candidates) != 1:
        raise RuntimeError('Cannot identify a single source model collection for %s' % path)
    collection = candidates[0]
    order = {o: i for i, o in enumerate(collection.objects)}
    meshes.sort(key=lambda obj: order.get(obj, 10**9))
    return {'objects': objects, 'meshes': meshes, 'rig': rigs[0], 'collection': collection}


def _classify_mesh(obj):
    """Hair shader covers scalp/beard; mixed material meshes need manual work."""
    name = clean_mesh(obj.data.name)
    by_name = bool(re.match(r'^[MS]_Hair(?:$|[_0-9])', name, re.IGNORECASE))
    shaders = []
    for material in obj.data.materials:
        if material is not None:
            shaders.append(_shader(material))
    known = [value for value in shaders if value is not None]
    hair = [value == 0x16 for value in known]
    if any(hair) and (not all(hair) or len(known) != len(shaders)):
        raise ValueError('Mesh %s mixes hair and other/unknown shader materials; split it explicitly first'
                         % obj.data.name)
    if by_name and known and not all(hair):
        raise ValueError('Mesh %s has a hair name but a nonhair shader; manual classification required'
                         % obj.data.name)
    if by_name or (known and all(hair)):
        return 'hair'
    if name.casefold() in {'m_body', 'm_head', 'm_head_caruncle', 'm_eye', 'm_teeth',
                           'm_tongue', 'm_mouthbag', 'm_transb_eyelash'}:
        raise ValueError('Attire contains BaseModel geometry %s; refuse to replace identity meshes' % name)
    # S_ is a simulation cage. Known hair cages use S_Hair above. Unknown
    # material-free cages are retained with donor clothing, not guessed away.
    return 'clothing'


def _body_counts(meshes):
    return {clean_mesh(o.data.name).casefold(): len(o.data.vertices) for o in meshes
            if clean_mesh(o.data.name).casefold() in ('m_body', 'm_head')}


def _donor_body_counts(job):
    """Vertex counts of the donor pack's own body/head, or None without a BaseModel.

    Imported into an empty scene and removed again, so the swap scene never
    holds two baseModels (the add-on looks bodies up by mesh name).
    """
    import bpy
    from model_layout import one_model
    folder = next((p for p in job['donor_character'].iterdir()
                   if p.is_dir() and p.name.casefold() == 'basemodel'), None)
    if folder is None:
        return None
    donor_base = _import_model(one_model(folder, base=True), 'donor_base_model')
    counts = _body_counts(donor_base['meshes'])
    for obj in donor_base['objects']:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return counts


def _apply_donor_hpl(job, base, donor_counts, warnings):
    """Hide the base body under the donor clothing with the donor's own HPL masks.

    HPL stores body vertex indices, so the masks are applied only when the
    donor pack's body and head have the same vertex counts as the base. The
    groups are added to the scene reference copy; the BaseModel files are still
    copied byte for byte, and the exporter embeds the masks in the new attire.
    """
    from WWE2K_MDL.Serialize.hpl_roundtrip import extract_hpl_from_mcd, apply_hpl_to_basemodel
    base_counts = _body_counts(base['meshes'])
    result = {'applied': False, 'base_counts': base_counts, 'donor_counts': donor_counts}
    if donor_counts is None:
        result['reason'] = 'The attire donor has no BaseModel to confirm its body matches the base'
    elif set(base_counts) != {'m_body', 'm_head'} or donor_counts != base_counts:
        result['reason'] = 'The donor body/head vertex counts differ from the base'
    else:
        hpl = extract_hpl_from_mcd(str(job['donor_attire']))
        if not hpl or len(hpl) <= 32:
            result['reason'] = 'The donor attire has no body hide-mask (HPL) data'
        else:
            ok, message = apply_hpl_to_basemodel(hpl)
            result.update(applied=bool(ok), message=message, hpl_bytes=len(hpl))
            if not ok:
                result['reason'] = message
    return result


def _clothing_visibility(obj):
    """(ring, entrance) visibility from the importer's appearance flag."""
    flag = int(getattr(getattr(obj, 'mdl_properties', None), 'appearance_flag', 128) or 128)
    entrance_only, ring_only = bool(flag & 1), bool(flag & 2)
    return (not entrance_only, not ring_only)


def _generate_hpl(base, clothing, outward=2.5, inward=4.0):
    """Hide base body vertices that the donor clothing covers, per ring/entrance.

    A body vertex is covered when the clothing surface lies within ``outward``
    cm along its outward normal, or within ``inward`` cm behind it (skin that
    pokes through tight clothing). The mask is eroded by one vertex ring so
    garment edges never open holes in the body. Groups go on the scene
    reference copy only; the BaseModel files are still copied byte for byte.
    """
    from mathutils.bvhtree import BVHTree
    sets = {'HPL_Ring': [], 'HPL_Entrance': []}
    for obj in clothing:
        if clean_mesh(obj.data.name).startswith('S_') or not obj.data.polygons:
            continue
        ring, entrance = _clothing_visibility(obj)
        if ring:
            sets['HPL_Ring'].append(obj)
        if entrance:
            sets['HPL_Entrance'].append(obj)
    trees = {}
    for name, objs in sets.items():
        verts, tris = [], []
        for obj in objs:
            offset = len(verts)
            verts.extend(obj.matrix_world @ v.co for v in obj.data.vertices)
            obj.data.calc_loop_triangles()
            tris.extend(tuple(offset + i for i in t.vertices) for t in obj.data.loop_triangles)
        trees[name] = BVHTree.FromPolygons(verts, tris, all_triangles=True) if tris else None
    result = {}
    for body in [o for o in base['meshes'] if clean_mesh(o.data.name).casefold() in ('m_body', 'm_head')]:
        mesh = body.data
        normal_matrix = body.matrix_world.to_3x3().inverted().transposed()
        points = [body.matrix_world @ v.co for v in mesh.vertices]
        normals = [(normal_matrix @ v.normal).normalized() for v in mesh.vertices]
        neighbors = [[] for _ in mesh.vertices]
        for edge in mesh.edges:
            a, b = edge.vertices
            neighbors[a].append(b)
            neighbors[b].append(a)
        counts = {}
        for name, tree in trees.items():
            if tree is None:
                continue
            covered = set()
            for i, (p, n) in enumerate(zip(points, normals)):
                if n.length < .5:
                    continue
                hit = tree.ray_cast(p + n * .01, n, outward)[0]
                if hit is None:
                    hit = tree.ray_cast(p - n * .01, -n, inward)[0]
                if hit is not None:
                    covered.add(i)
            masked = [i for i in covered if all(j in covered for j in neighbors[i])]
            if masked:
                group = body.vertex_groups.get(name) or body.vertex_groups.new(name=name)
                group.add(masked, 1.0, 'REPLACE')
            counts[name] = len(masked)
        result[clean_mesh(mesh.name)] = {'vertices': len(points), 'masked': counts}
    return result


def _body_hide_masks(job, base, clothing, donor_counts, warnings):
    """Use the donor's own HPL when it fits the base body, else generate masks."""
    result = _apply_donor_hpl(job, base, donor_counts, warnings)
    if not result['applied']:
        result['donor_masks_skipped'] = result.pop('reason', None)
        result['generated'] = _generate_hpl(base, clothing)
        result['applied'] = any(sum(v['masked'].values()) for v in result['generated'].values())
        if not result['applied']:
            warnings.append('No body hide-masks (HPL) could be made; skin may show through the clothing.')
    return result


def _copy_attachments(source_collection, bundle):
    import bpy
    source = next((c for c in source_collection.children if c.name.startswith('Model APs')), None)
    if source is None:
        return [], {'objects': 0, 'skipped_missing_bones': []}
    collection = bpy.data.collections.new('Model APs Attire Swap')
    bundle['collection'].children.link(collection)
    copies, skipped = {}, []
    bones = set(bundle['armature'].data.bones.keys())
    for old in source.objects:
        # An attachment requiring an absent bone must not quietly bind elsewhere.
        required = {target.subtarget for constraint in old.constraints if constraint.type == 'ARMATURE'
                    for target in constraint.targets if target.subtarget}
        if required - bones:
            skipped.append({'attachment': old.name, 'bones': sorted(required - bones)})
            continue
        new = old.copy()
        new.data = old.data.copy() if old.data else None
        collection.objects.link(new)
        copies[old] = new
    if skipped:
        raise ValueError('Donor attachments use missing base bones: %r' % skipped)
    for old, new in copies.items():
        if old.parent in copies:
            new.parent = copies[old.parent]
        elif old.parent and old.parent.type == 'ARMATURE':
            new.parent = bundle['armature']
        for constraint in new.constraints:
            if constraint.type == 'ARMATURE':
                for target in constraint.targets:
                    target.target = bundle['armature']
    return list(copies.values()), {'objects': len(copies), 'skipped_missing_bones': skipped}


def _material_signature(mat):
    images = sorted(Path(node.image.filepath).name.casefold()
                    for node in mat.node_tree.nodes if node.type == 'TEX_IMAGE' and node.image) if mat.node_tree else []
    # Imported materials can contain ID properties that aren't JSON serializable.
    metadata = {key: str(mat.get(key, '')) for key in
                ('vc_2k26_shader_byte', 'vc_2k26_template', 'vc_2k26_params')}
    return json.dumps({'metadata': metadata, 'images': images}, sort_keys=True)


def _canonicalize_materials(bundle, reference_objects):
    """The supplied exporter globally looks materials up by canonical slot name."""
    import bpy
    from merge_helpers import clean_material_name
    used = {}
    provenance = {}
    for obj in bundle['meshes']:
        role = 'base_hair' if obj in bundle['hair'] else 'donor_clothing'
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None:
                continue
            key = clean_material_name(mat.name)
            folded = key.casefold()
            if folded in used and used[folded] is not mat:
                canonical = used[folded]
                signature_equal = _material_signature(canonical) == _material_signature(mat)
                inert_cage_placeholder = (folded == 'lambert1' and _shader(canonical) is None
                                           and _shader(mat) is None and signature_equal)
                if not inert_cage_placeholder and (provenance[folded] != role or not signature_equal):
                    raise ValueError('Different required materials share engine slot %s; rename in source first' % key)
                slot.material = canonical
            else:
                used[folded] = mat
                provenance[folded] = role
    # Reference materials may share a name; rename them so global lookup cannot
    # select a BaseModel/reference slot instead of the intended attire material.
    for folded, selected in used.items():
        key = clean_material_name(selected.name)
        for index, other in enumerate(list(bpy.data.materials)):
            if other is not selected and clean_material_name(other.name).casefold() == folded:
                other.name = key + '_SOURCE_REFERENCE_' + str(index)
        selected.name = key
    return [(mat, provenance[folded]) for folded, mat in used.items()]


def _fnv(value):
    result = 0xCBF29CE484222325
    for char in value.lower():
        result = ((result ^ (ord(char) & 255)) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return result


def _params(mat):
    value = mat.get('vc_2k26_params', '{}')
    if hasattr(value, 'to_dict'):
        value = value.to_dict()
    elif isinstance(value, str):
        value = json.loads(value or '{}')
    if not isinstance(value, dict):
        raise ValueError('Invalid material metadata on %s' % mat.name)
    return value


def _texture_directory(attire):
    return next((p for p in Path(attire).parent.iterdir()
                 if p.is_dir() and p.name.casefold() == 'textures'), None)


def _prepare_textures(job, target, materials):
    """Copy only required source textures and remap every known local hash.

    Unknown hashes remain unchanged: shaders also legitimately reference shared
    engine textures. No procedural hair maps or shader defaults are invented.
    """
    from merge_helpers import clean_material_name
    target.mkdir(parents=True, exist_ok=True)
    catalogs = {}
    for role, model, character in (
            ('base_hair', job['base_attire'], job['base_character']),
            ('donor_clothing', job['donor_attire'], job['donor_character'])):
        directory = _texture_directory(model)
        files = [p for p in directory.iterdir() if p.is_file()] if directory else []
        names = {p.name.casefold(): p for p in files}
        hashes = {}
        for path in files:
            relative = 'Characters/%s/%s/Textures/%s.tex' % (character.name, model.parent.name, path.stem)
            hashes[_fnv(relative)] = path
        catalogs[role] = (names, hashes)
    output_prefix = 'Characters/%s/%s/Textures/' % (job['base_character'].name, job['base_attire'].parent.name)
    copied, mapping_report = {}, []

    def copy_file(source):
        key = source.name.casefold()
        digest = sha256(source)
        if key in copied and copied[key]['sha256'] != digest:
            raise ValueError('Base hair and donor clothes require different textures named %s; refusing overwrite'
                             % source.name)
        destination = target / source.name
        if key not in copied:
            shutil.copy2(source, destination)
            copied[key] = {'source': str(source), 'filename': source.name, 'sha256': digest}
        return target / copied[key]['filename']

    for mat, role in materials:
        names, hashes = catalogs[role]
        params = _params(mat)
        local_refs = []
        expected_primary = {}

        def remap(value, key=''):
            if isinstance(value, dict):
                return {k: remap(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [remap(v, key) for v in value]
            if key.endswith('@ref') and isinstance(value, int) and value in hashes:
                source = hashes[value]
                copied_path = copy_file(source)
                replaced = _fnv(output_prefix + copied_path.stem + '.tex')
                local_refs.append({'field': key, 'source': value, 'output': replaced, 'texture': copied_path.name})
                return replaced
            return value

        params = remap(params)
        # Regular gear import stores only primary hashes outside params; use
        # those too when their actual source texture exists in this attire.
        for attribute, field in (('vc_2k26_color_map_hash', 'colorMap@ref'),
                                 ('vc_2k26_normal_map_hash', 'nrMap@ref')):
            value = mat.get(attribute)
            if value is not None:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue
                if value in hashes:
                    params[field] = remap(value, field)
        bound = []
        if mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    filename = Path(node.image.filepath.replace('\\', '/')).name.casefold()
                    source = names.get(filename)
                    if source:
                        destination = copy_file(source)
                        node.image.filepath = str(destination)
                        bound.append(destination.name)
        # The importer may not load a BC7 image into Blender; metadata/prefix
        # copying still preserves these raw DDS files for the actual formatter.
        stem = clean_material_name(mat.name)
        stem = re.sub(r'(?i)_alpha$', '', stem).casefold()
        prefix_files = [p for p in names.values()
                        if p.stem.casefold() == stem or p.stem.casefold().startswith(stem + '_')]
        for source in prefix_files:
            copy_file(source)
        # Gear color hashes imported from external scope may be stale/unknown;
        # use the actual local texture when its unambiguous slot name exists.
        # Hair Type 1/2 computes color procedurally and must retain that schema.
        if _shader(mat) == 0x15 or (_shader(mat) == 0x16 and params.get('hairType') == 3):
            for field, suffix in (('colorMap@ref', '_color'), ('nrMap@ref', '_nrm')):
                matches = [p for p in prefix_files if p.stem.casefold() == stem + suffix]
                if len(matches) == 1:
                    params[field] = _fnv(output_prefix + copy_file(matches[0]).stem + '.tex')
                    expected_primary['color_map_hash' if field == 'colorMap@ref' else 'normal_map_hash'] = params[field]
        # A source texture identified by an existing hash is equally authoritative
        # when the texture filename does not follow the slot-name convention.
        for item in local_refs:
            if item['field'] in ('colorMap@ref', 'nrMap@ref', 'g_nrmMap@ref'):
                field = 'color_map_hash' if item['field'] == 'colorMap@ref' else 'normal_map_hash'
                expected_primary.setdefault(field, item['output'])
        mat['vc_2k26_params'] = json.dumps(params)
        mapping_report.append({'material': mat.name, 'role': role, 'shader': _shader(mat),
                               'remapped_local_refs': local_refs, 'image_bindings': bound,
                               'expected_primary_refs': expected_primary})
    output_hashes = {_fnv(output_prefix + Path(item['filename']).stem + '.tex'): item['filename']
                     for item in copied.values()}
    return {'textures': list(copied.values()), 'materials': mapping_report,
            'output_texture_hashes': output_hashes}


class _Tee(io.TextIOBase):
    def __init__(self, stream):
        self.stream, self.chunks = stream, []
    def write(self, value):
        self.chunks.append(value)
        self.stream.write(value)
        return len(value)
    def flush(self):
        self.stream.flush()


def _fit(body_parts, bundle, enabled, report, warnings):
    if not enabled:
        report['fit'] = {'enabled': False, 'objects': []}
        return
    from fit_helpers import FitLimitError
    from garment_fit import fit_garments
    fits = []
    total = len(bundle['clothing'])
    for index, obj in enumerate(bundle['clothing']):
        name = clean_mesh(obj.data.name)
        progress(.49 + .20 * index / max(total, 1), 'Checking clothing fit: ' + name)
        if name.startswith('S_'):
            fits.append({'mesh': name, 'status': 'simulation_cage_unchanged'})
            continue
        try:
            if name.casefold().startswith(('m_jacket', 'm_tights')):
                options = dict(clearance=.5, max_displacement=3.7,
                               surface_iterations=8, surface_clearance=.2)
            elif 'wrist' in name.casefold():
                options = dict(clearance=.3, max_displacement=2,
                               surface_iterations=20, surface_clearance=.2)
            else:
                options = dict(clearance=.22, max_displacement=2,
                               surface_iterations=20, surface_clearance=.12)
            item = fit_garments(body_parts, [obj], **options)
            detail = item['objects'][0]
            if detail['interior_item']:
                # Mouthguards and linings sit inside the body by design.
                status = 'interior_item_unchanged'
            elif detail['fully_resolved']:
                status = 'applied'
            else:
                # Every vertex stayed within the same travel limit; the ones
                # that would need more were left for manual review.
                status = 'applied_partial'
                warnings.append(
                    'Automatic fit moved %s within its %.1f cm limit; %d vertices and %d sampled '
                    'surface points remain inside the body (deepest %.1f cm). Inspect it.'
                    % (name, options['max_displacement'], detail['unresolved_inside_vertices'],
                       detail['unresolved_surface_samples'], detail['deepest_remaining_penetration_cm']))
            fits.append({'mesh': name, 'status': status, 'report': item})
        except (FitLimitError, ValueError) as error:
            # The helper computes proposals atomically, so failed garments are
            # unchanged. A failed safe fit cannot justify larger guessed edits.
            fits.append({'mesh': name, 'status': 'unchanged_unsafe_fit', 'reason': str(error),
                         'report': getattr(error, 'report', None)})
            warnings.append('Automatic fit left %s unchanged: %s' % (name, error))
    report['fit'] = {'enabled': True, 'rest_pose_only': True, 'objects': fits}


def _restore_base_hair_materials(job, attire, report):
    """Ship the base attire's own .jmtl for every preserved hair material.

    The exporter regenerates hair materials from a template and drops their
    flow/root (``*_flow``, ``*_rai``) texture references, which makes hair
    invisible or see-through in game. Preserved hair keeps the base character
    and base attire folder names, so the original material's texture hashes
    still resolve. Each original is used only when its shader matches the
    generated file and every local texture it references is shipped.
    """
    from WWE2K_MDL.Blender.Extras.jmtl_resolver import extract_full_material_metadata
    from merge_helpers import clean_material_name
    source_dir = next((p for p in job['base_attire'].parent.iterdir()
                       if p.is_dir() and p.name.casefold() == 'materials'), None)
    output_dir = attire / 'Materials'
    if source_dir is None or not output_dir.is_dir():
        return []
    hair = {clean_material_name(entry['material']).casefold()
            for entry in report['textures']['materials'] if entry['role'] == 'base_hair'}
    originals = {}
    for path in source_dir.glob('*.jmtl'):
        originals.setdefault(path.stem.casefold(), []).append(path)
    prefix = 'Characters/%s/%s/Textures/' % (job['base_character'].name, job['base_attire'].parent.name)
    source_textures = _texture_directory(job['base_attire'])
    local = ({_fnv(prefix + p.stem + '.tex'): p for p in source_textures.iterdir() if p.is_file()}
             if source_textures else {})
    target_textures = attire / 'Textures'
    restored = []
    for generated_path in sorted(output_dir.glob('*.jmtl')):
        key = generated_path.stem.casefold()
        if key not in hair or len(originals.get(key, [])) != 1:
            continue
        original = originals[key][0].read_bytes()
        generated = generated_path.read_bytes()
        before = extract_full_material_metadata(original)
        after = extract_full_material_metadata(generated)
        if not before or not after or before.get('shader_byte') != after.get('shader_byte'):
            continue
        refs = {struct.unpack_from('<Q', original, i)[0] for i in range(len(original) - 7)}
        textures = sorted({local[value].name for value in refs if value in local})
        for name in textures:
            destination = target_textures / name
            if not destination.is_file():
                target_textures.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_textures / name, destination)
            elif sha256(destination) != sha256(source_textures / name):
                raise ValueError('Hair texture %s differs from the base attire copy' % name)
        generated_path.write_bytes(original)
        restored.append({'material': generated_path.name, 'source': str(originals[key][0]),
                         'generated_bytes': len(generated), 'restored_bytes': len(original),
                         'sha256': hashlib.sha256(original).hexdigest(), 'textures': textures})
    return restored


def _validate_exported_material_refs(attire, report):
    """Verify exported local primary maps resolve; reject stale known refs."""
    from WWE2K_MDL.Blender.Extras.jmtl_resolver import extract_full_material_metadata
    material_dir = attire / 'Materials'
    values = []
    expected = {}
    by_material = {entry['material'].casefold(): entry.get('expected_primary_refs', {})
                   for entry in report['textures']['materials']}
    for entry in report['textures']['materials']:
        for item in entry['remapped_local_refs']:
            expected[item['source']] = item['output']
    for path in material_dir.glob('*.jmtl'):
        data = path.read_bytes()
        # Remaining *known* source-local refs are never safe to ship. Only patch
        # unique u64 occurrences and keep byte size / FlatBuffer layout intact.
        patched = []
        for old, new in expected.items():
            if old == new:
                continue
            needle, replacement = struct.pack('<Q', old), struct.pack('<Q', new)
            count = data.count(needle)
            if count > 1:
                raise ValueError('Ambiguous repeated stale texture hash in %s' % path.name)
            if count == 1:
                data = data.replace(needle, replacement)
                patched.append({'from': old, 'to': new})
        if patched:
            path.write_bytes(data)
        metadata = extract_full_material_metadata(data)
        if metadata is None:
            raise RuntimeError('Cannot read generated material %s' % path.name)
        # Node extraction can override explicit params with a stale imported
        # image-name hash. Verify the serialized primary field against the actual
        # copied texture, then replace its unique u64 without changing layout.
        for field, wanted in by_material.get(path.stem.casefold(), {}).items():
            actual = metadata.get(field)
            if actual == wanted:
                continue
            if actual in (None, 0, 0xFFFFFFFFFFFFFFFF):
                raise ValueError('Generated %s omitted required local %s' % (path.name, field))
            needle = struct.pack('<Q', actual)
            if data.count(needle) != 1:
                raise ValueError('Cannot uniquely correct %s in %s' % (field, path.name))
            data = data.replace(needle, struct.pack('<Q', wanted))
            patched.append({'field': field, 'from': actual, 'to': wanted})
        if patched:
            path.write_bytes(data)
            metadata = extract_full_material_metadata(data)
        hashes = {int(key): value for key, value in report['textures']['output_texture_hashes'].items()}
        resolution = {}
        for field in ('color_map_hash', 'normal_map_hash'):
            value = (metadata or {}).get(field)
            if value not in (None, 0, 0xFFFFFFFFFFFFFFFF):
                resolution[field] = {'hash': value, 'texture': hashes.get(value),
                                     'local_file_resolves': value in hashes}
        values.append({'file': path.name, 'corrected_stale_local_refs': patched,
                       'metadata': metadata, 'primary_ref_resolution': resolution})
    if not values:
        raise RuntimeError('The addon did not generate any 2K26 material files')
    report['exported_materials'] = values


def run(config):
    import bpy
    progress(.01, 'Checking source models')
    job = validate_job(config)
    output = job['output_dir']
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.with_name('.' + output.name + '.staging-' + uuid.uuid4().hex[:10])
    stage.mkdir()
    source_hashes = {k: sha256(job[k]) for k in ('base_model', 'base_attire', 'donor_attire')}
    report = {'draft': True, 'source_models': {k: str(job[k]) for k in source_hashes},
              'source_model_sha256': source_hashes, 'versions': job['versions']}
    warnings = ['DRAFT: Hair and clothing physics require external YCL retargeting and an in-game test.',
                'No source YCL or WDF was copied. The 3D preview shows poses, not physics simulation.',
                'Automatic clothing fitting checks sampled rest-pose geometry; animated clipping is unverified.']
    try:
        sys.path.insert(0, str(job['helpers_dir']))
        sys.path.insert(0, str(job['addon_dir'].parent))
        import WWE2K_MDL
        WWE2K_MDL.register()
        import merge_helpers as mh
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        progress(.05, 'Checking the donor body for hide-masks')
        donor_body_counts = _donor_body_counts(job)
        progress(.08, 'Importing base body and face')
        base = _import_model(job['base_model'], 'base_model')
        progress(.20, 'Importing preserved hair')
        original_attire = _import_model(job['base_attire'], 'base_attire')
        progress(.32, 'Importing donor clothing')
        donor = _import_model(job['donor_attire'], 'donor_attire')
        base_classes = {o: _classify_mesh(o) for o in original_attire['meshes']}
        donor_classes = {o: _classify_mesh(o) for o in donor['meshes']}
        hair = [o for o in original_attire['meshes'] if base_classes[o] == 'hair']
        clothing = [o for o in donor['meshes'] if donor_classes[o] == 'clothing']
        if not hair:
            raise ValueError('No base hair/scalp/beard was confidently identified; choose a supported base attire')
        if not clothing:
            raise ValueError('No donor clothing was identified')
        if 'J_Hair' not in original_attire['rig'].data.bones:
            raise ValueError('Base attire rig has no J_Hair subtree; manual rig setup required')
        report['selection'] = {'base_hair': [o.data.name for o in hair],
                               'donor_clothing': [o.data.name for o in clothing],
                               'excluded_donor_hair': [o.data.name for o in donor['meshes']
                                                       if donor_classes[o] == 'hair']}
        progress(.43, 'Combining clothing with the base rig')
        bundle = mh.make_export_bundle(original_attire['rig'], original_attire['rig'], hair, clothing,
                                       str(job['donor_attire']), name='ATTIRE_SWAP_EXPORT',
                                       donor_collection=donor['collection'])
        report['initial_export_validation'] = mh.validate_export_bundle(bundle)
        report['hair_geometry'] = []
        for source, target in zip(hair, bundle['hair']):
            delta = max(((source.matrix_world @ a.co) - (target.matrix_world @ b.co)).length
                        for a, b in zip(source.data.vertices, target.data.vertices))
            report['hair_geometry'].append({'mesh': clean_mesh(source.data.name),
                                            'vertices': len(source.data.vertices), 'max_world_change': delta})
            if delta > 1e-5:
                raise RuntimeError('Preserved base hair geometry changed')
        bodies = [o for o in base['meshes'] if clean_mesh(o.data.name).casefold() == 'm_body']
        if len(bodies) != 1:
            raise ValueError('Expected exactly one base M_Body mesh for clothing fit')
        progress(.49, 'Fitting clothing without changing the body')
        heads = [o for o in base['meshes'] if clean_mesh(o.data.name).casefold() == 'm_head']
        if len(heads) != 1:
            raise ValueError('Expected exactly one base M_Head for the combined garment collider')
        _fit([bodies[0], heads[0]], bundle, job['fit_clothing'], report, warnings)
        attachments, report['attachments'] = _copy_attachments(donor['collection'], bundle)
        keep = set(bundle['meshes'] + [bundle['armature']] + base['objects'] + attachments)
        for obj in list(bpy.data.objects):
            if obj not in keep:
                bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in list(bpy.data.meshes):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for mat in list(bpy.data.materials):
            if mat.users == 0:
                bpy.data.materials.remove(mat)
        materials = _canonicalize_materials(bundle, base['objects'])
        # Legacy HPL masks belong to the replaced attire. Remove annotation
        # groups on the imported reference copy only; original BaseModel is copied.
        for obj in base['meshes']:
            for group in list(obj.vertex_groups):
                if group.name.startswith('HPL_'):
                    obj.vertex_groups.remove(group)
        report['body_hide_masks'] = _body_hide_masks(job, base, bundle['clothing'], donor_body_counts, warnings)
        character = stage / 'Characters' / job['base_character'].name
        attire = character / job['base_attire'].parent.name
        attire.mkdir(parents=True)
        source_base = job['base_model'].parent
        original_hashes = _manifest(source_base)
        shutil.copytree(source_base, character / source_base.name)
        report['base_model_sha256'] = original_hashes
        report['base_material_repairs'] = _repair_missing_eyelash(
            character / source_base.name, job['base_character'].name, job['repair_eyelash'])
        report['textures'] = _prepare_textures(job, attire / 'Textures', materials)
        # Adapt donor raw hair template when available, using only base colliders.
        # This still does not create a working YCL or claim physics are repaired.
        hywa = job['donor_attire'].with_suffix('.hair_ywa')
        hcyw = job['donor_attire'].with_suffix('.hair_corr_ywa')
        if hywa.is_file() and hcyw.is_file():
            template = stage / '_review' / 'physics-template'
            report['physics_template'] = mh.prepare_hair_physics_directory(
                template, job['base_model'], hywa, hcyw, basename='HairTemplate')
            report['physics_settings'] = mh.configure_hair_physics_swap(template, basename='HairTemplate')
        else:
            bpy.context.scene.vcmodel_physics_swap.enabled = False
            report['physics_template'] = {'enabled': False, 'reason': 'Complete donor hair sidecars unavailable'}
            warnings.append('Donor hair template sidecars were unavailable; no hair solver retarget was attempted.')
        report['export_validation'] = mh.validate_export_bundle(bundle)
        progress(.74, 'Exporting WWE 2K26 draft')
        mh.select_export_bundle(bundle)
        exported_mcd = attire / (job['base_attire'].stem + '.mcd')
        tee = _Tee(sys.stdout)
        with contextlib.redirect_stdout(tee):
            result = bpy.ops.export_vcskinmodel.save(
                filepath=str(exported_mcd), compile_target='option_vcexport_26', generate_mtls=True,
                overwrite_existing_jmtls=True, export_hpl=False, export_atpts=True,
                export_gtpts=False, preserve_normals=True, preserve_tangents=True,
                use_vertex_remap=True, generate_ctg=False)
        report['export_log'] = ''.join(tee.chunks)
        if result != {'FINISHED'} or not exported_mcd.is_file():
            raise RuntimeError('MCD draft export failed: %s' % result)
        # Addon may use the canonical Attire name rather than requested ID name.
        target_mtls = exported_mcd.with_suffix('.mtls')
        if not target_mtls.is_file():
            candidates = list(attire.glob('*.mtls'))
            if len(candidates) != 1:
                raise RuntimeError('Cannot identify generated MTLS library')
            candidates[0].rename(target_mtls)
        report['restored_base_hair_materials'] = _restore_base_hair_materials(job, attire, report)
        _validate_exported_material_refs(attire, report)
        unresolved_refs = [(entry['file'], field) for entry in report['exported_materials']
                           for field, value in entry['primary_ref_resolution'].items()
                           if not value['local_file_resolves']]
        if unresolved_refs:
            warnings.append('Primary material maps with no copied local DDS (may be engine shared maps): %s'
                            % ', '.join('%s:%s' % item for item in unresolved_refs))
        # The base hair is shipped unchanged, so its own YCL physics is carried
        # over byte for byte (never edited). Clothing cages in it belong to the
        # base attire; when the donor's clothing differs, test it in game.
        base_ycl = [p for p in job['base_attire'].parent.iterdir() if p.is_file()
                    and p.suffix.casefold() == '.ycl' and p.stem.casefold() == job['base_attire'].stem.casefold()]
        ycl_copy = None
        if len(base_ycl) == 1:
            target_ycl = exported_mcd.with_suffix('.ycl')
            shutil.copy2(base_ycl[0], target_ycl)
            if sha256(target_ycl) != sha256(base_ycl[0]):
                raise RuntimeError('Base YCL copy could not be verified')
            ycl_copy = {'source': str(base_ycl[0]), 'file': target_ycl.name, 'sha256': sha256(target_ycl)}
            warnings[1] = ('Base hair physics (YCL) was copied unchanged from the base attire. '
                           'The 3D preview shows poses, not physics simulation.')
            if job['donor_attire'] != job['base_attire']:
                warnings.append('The copied base YCL also describes the base attire\'s own clothing; '
                                'check donor clothing physics in game.')
        else:
            warnings[1] = ('The base attire has no YCL to carry over; no physics file was added. '
                           'The 3D preview shows poses, not physics simulation.')
        data = exported_mcd.read_bytes()
        report['physics'] = {'status': 'unverified/static_draft', 'ycl_created': False,
                             'base_ycl_copied': ycl_copy,
                             'wdf_copied': False,
                             'embedded_stream_markers_present': {tag: tag.encode('ascii') in data
                                                                for tag in ('HYWA', 'HCYW', 'OYWA', 'OCYW')},
                             'exporter_messages': [line for line in report['export_log'].splitlines()
                                                   if any(term in line.casefold() for term in
                                                          ('dropped stale', 'static', 'physics', 'oob'))]}
        report['output_base_model_sha256'] = _verify_copied_base(
            source_base, character / source_base.name, original_hashes, report['base_material_repairs'])
        report['base_model_preservation'] = {
            'byte_identical': not report['base_material_repairs'],
            'geometry_and_textures_byte_identical': True,
            'allowed_changed_paths': [item['relative_path'] for item in report['base_material_repairs']]}
        if any(sha256(job[key]) != digest for key, digest in source_hashes.items()):
            raise RuntimeError('Source model files changed during the job')
        progress(.90, 'Saving review project and verification report')
        for obj in bundle['meshes']:
            obj.hide_render = clean_mesh(obj.data.name).startswith('S_') or obj.mdl_properties.appearance_flag == 129
            obj['swap_role'] = 'Preserved base hair' if obj in bundle['hair'] else 'Donor clothing'
        for image in bpy.data.images:
            if image.source == 'FILE':
                try:
                    relative = Path(image.filepath).relative_to(stage)
                    image.filepath = str(output / relative)
                except ValueError:
                    pass
            if image.has_data and image.source == 'FILE':
                try:
                    image.pack()
                except RuntimeError:
                    pass
        if hasattr(bpy.context.scene, 'vcmodel_physics_swap') and hywa.is_file() and hcyw.is_file():
            bpy.context.scene.vcmodel_physics_swap.donor_directory = str(output / '_review' / 'physics-template')
        blend = stage / 'AttireSwap_Draft.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(blend), compress=True)
        report['warnings'] = warnings
        _json(stage / 'swap-report.json', report)
        base_note = ('BaseModel geometry/textures preserved; output eyelash material binding repaired (see report).'
                     if report['base_material_repairs'] else 'BaseModel copied byte-for-byte.')
        (stage / 'DRAFT_NOT_GAME_READY.txt').write_text(
            'WWE 2K26 ATTIRE SWAP — VISUAL DRAFT\n\n' + '\n'.join(warnings) +
            '\n\n' + base_note + ' Base hair/scalp/beard geometry preserved.\n'
            'YCL/WDF solver retargeting and in-game validation are still required.\n', encoding='utf-8')
        # Final publication is one directory rename; source paths are never used
        # as export targets, and a cancelled job leaves only a staging directory.
        if output.exists():
            if any(output.iterdir()):
                raise FileExistsError('Output became nonempty while job was running')
            output.rmdir()
        stage.rename(output)
        result = {'success': True, 'draft': True, 'output_root': str(output),
                  'output_character': str(output / character.relative_to(stage)),
                  'output_attire': job['base_attire'].parent.name,
                  'output_model': str(output / exported_mcd.relative_to(stage)),
                  'blend_path': str(output / blend.relative_to(stage)),
                  'report_path': str(output / 'swap-report.json'), 'warnings': warnings,
                  'base_material_repairs': report['base_material_repairs'],
                  'physics_status': 'unverified/static_draft'}
        _json(output / 'swap-result.json', result)
        progress(1.0, 'Draft swap ready for 3D review')
        return result
    except BaseException as error:
        report['warnings'] = warnings
        report['failure_traceback'] = traceback.format_exc()
        _json(stage / 'swap-report.json', report)
        error.partial_output = str(stage)
        raise


def main():
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    path = args[0] if args else os.environ.get('SWAP_JOB_PATH')
    if not path:
        raise ValueError('Supply a job JSON after Blender -- separator')
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    explicit_result = config.get('result_path')
    result_path = Path(explicit_result or (Path(config['output_dir']) / 'swap-result.json'))
    if result_path.exists():
        raise FileExistsError('Refusing to overwrite an existing result file: %s' % result_path)
    try:
        result = run(config)
    except BaseException as error:
        result = {'success': False, 'draft': True, 'error': str(error),
                  'traceback': traceback.format_exc(), 'physics_status': 'unverified/static_draft',
                  'partial_output': getattr(error, 'partial_output', None)}
        # A rejected, already-populated destination must stay untouched even
        # when the caller did not supply a separate result_path.
        if not explicit_result and result_path.parent.exists() and any(result_path.parent.iterdir()):
            result_path = Path(path).with_name(Path(path).stem + '-failed-result.json')
            if result_path.exists():
                result_path = result_path.with_name(result_path.stem + '-' + uuid.uuid4().hex[:8] + '.json')
        _json(result_path, result)
        print('SWAP_RESULT ' + json.dumps(result), flush=True)
        raise
    _json(result_path, result)
    print('SWAP_RESULT ' + json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
