"""Blender helpers for Port appearance + ALL IN clothes.

Import this file inside Blender after registering WWE2K_MDL.  These functions
operate on explicit caller-provided object lists; they do not guess which
meshes are hair, facial hair, clothing, or simulation cages.  Originals are
kept as references.  No import or export operator is called here.

Recommended order:
  1. Import Port BaseModel, Port attire, and ALL IN attire separately.
  2. Identify ALL hair-related Port meshes (render, cages, facial hair) and
     donor clothing meshes, preserving their object/data names and flags.
  3. make_export_bundle(...), then canonicalize_port_hair_materials(...).
  4. prepare_hair_physics_directory(...) and configure_hair_physics_swap(...).
  5. select_export_bundle(...), review validate_export_bundle(...), export
     with the addon's 2K26 attire exporter and material generation enabled.

The physics swap adapts ALL IN HYWA/HCYW templates to Port geometry.  It does
not reproduce Port .ycl simulation.  This addon has no .ycl reader/writer;
the caller must separately resolve the final .ycl file and test in game.
The Port BaseModel files should be copied byte-for-byte to the final package,
not re-exported.  Keep Port BaseModel head/body in the scene as references,
outside export selection.  Do not load the ALL IN BaseModel into this scene:
the addon's scene-collider scanner uses the first matching mesh name.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil

import bpy


def _require_object_mode():
    active = bpy.context.view_layer.objects.active
    if active is not None and active.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')


def _copy_id_properties(source, destination):
    for key in source.keys():
        if key == '_RNA_UI':
            continue
        value = source[key]
        try:
            destination[key] = value.to_dict() if hasattr(value, 'to_dict') else value
        except (TypeError, ValueError):
            # Imported bones currently have no required addon ID properties.
            # Fail instead of silently dropping future required metadata.
            raise RuntimeError('Cannot preserve ID property %r on %s' % (key, source.name))


def _subtree_names(armature, root_name):
    if armature.type != 'ARMATURE':
        raise TypeError('%s is not an armature' % armature.name)
    root = armature.data.bones.get(root_name)
    if root is None:
        raise ValueError('%s has no %s bone' % (armature.name, root_name))
    queue = [root]
    out = []
    while queue:
        bone = queue.pop(0)
        out.append(bone.name)
        queue.extend(bone.children)
    return out


def _matrix_error(a, b):
    return max(abs(a[row][col] - b[row][col]) for row in range(4) for col in range(4))


def compare_nonhair_rest_matrices(port_armature, clothing_armature, root_name='J_Hair'):
    """Diagnostic only: compare common nonhair bones in world rest space."""
    skip = set(_subtree_names(port_armature, root_name))
    if root_name in clothing_armature.data.bones:
        skip.update(_subtree_names(clothing_armature, root_name))
    differences = []
    for source in port_armature.data.bones:
        dest = clothing_armature.data.bones.get(source.name)
        if dest is None or source.name in skip:
            continue
        error = _matrix_error(port_armature.matrix_world @ source.matrix_local,
                              clothing_armature.matrix_world @ dest.matrix_local)
        if error > 1e-5:
            differences.append({'bone': source.name, 'max_matrix_difference': error})
    return differences


def graft_port_hair_subtree(port_armature, export_armature, root_name='J_Hair'):
    """Replace ONLY the export rig's hair subtree with exact Port rest transforms.

    The export armature must be an independent copy, not the original donor.
    Its nonhair bones and matrices remain unchanged.  Port rest transforms are
    converted to export-armature coordinates, preserving world-space transforms
    even if armature objects have different world matrices.  Existing donor hair
    bones are removed rather than retained when their names happen to match.
    """
    if port_armature is export_armature or port_armature.data is export_armature.data:
        raise ValueError('Port and export armatures must have independent data')
    _require_object_mode()
    bpy.context.view_layer.update()
    source_names = _subtree_names(port_armature, root_name)
    donor_names = (set(_subtree_names(export_armature, root_name))
                   if root_name in export_armature.data.bones else set())
    conversion = export_armature.matrix_world.inverted() @ port_armature.matrix_world
    # Snapshot before entering Edit Mode: source edit_bones is not accessible
    # unless it too is edited, and we deliberately leave the source untouched.
    records = []
    for name in source_names:
        bone = port_armature.data.bones[name]
        records.append({
            'name': name,
            'parent': bone.parent.name if bone.parent else None,
            'matrix': conversion @ bone.matrix_local,
            'head': conversion @ bone.head_local,
            'tail': conversion @ bone.tail_local,
            'use_deform': bone.use_deform,
            'use_inherit_rotation': bone.use_inherit_rotation,
            'inherit_scale': bone.inherit_scale,
            'use_local_location': bone.use_local_location,
            'use_relative_parent': bone.use_relative_parent,
            'envelope_distance': bone.envelope_distance,
            'envelope_weight': bone.envelope_weight,
            'head_radius': bone.head_radius,
            'tail_radius': bone.tail_radius,
            'use_envelope_multiply': bone.use_envelope_multiply,
            'properties': {key: bone[key] for key in bone.keys() if key != '_RNA_UI'},
        })
    nonhair_before = {b.name: b.matrix_local.copy() for b in export_armature.data.bones
                      if b.name not in donor_names}
    # Replacing this subtree also deletes children regardless of their names.
    # Require every new root parent to exist outside the removed subtree.
    root_parent = records[0]['parent']
    if root_parent and (root_parent not in export_armature.data.bones or root_parent in donor_names):
        raise ValueError('Port hair root parent %r is absent from clothing rig' % root_parent)
    for rec in records:
        if rec['name'] in export_armature.data.bones and rec['name'] not in donor_names:
            raise ValueError('Port hair name collides with nonhair bone %s' % rec['name'])

    bpy.ops.object.select_all(action='DESELECT')
    export_armature.hide_set(False)
    export_armature.select_set(True)
    bpy.context.view_layer.objects.active = export_armature
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        edit = export_armature.data.edit_bones
        # Delete descendants before ancestors; no remaining nonhair bone is
        # implicitly reparented by removal.
        for name in reversed(list(_subtree_names_from_edit(edit, root_name))):
            edit.remove(edit[name])
        for rec in records:
            bone = edit.new(rec['name'])
            bone.head = rec['head']
            bone.tail = rec['tail']
            if bone.length <= 1e-8:
                raise ValueError('Zero-length Port hair bone %s' % rec['name'])
            bone.matrix = rec['matrix']
            parent = rec['parent']
            if parent:
                if parent not in edit:
                    raise ValueError('Missing hair parent %s for %s' % (parent, rec['name']))
                bone.parent = edit[parent]
            bone.use_connect = False  # prevent Blender snapping the preserved head
            bone.use_deform = rec['use_deform']
            bone.use_inherit_rotation = rec['use_inherit_rotation']
            bone.inherit_scale = rec['inherit_scale']
            bone.use_local_location = rec['use_local_location']
            bone.use_relative_parent = rec['use_relative_parent']
            bone.envelope_distance = rec['envelope_distance']
            bone.envelope_weight = rec['envelope_weight']
            bone.head_radius = rec['head_radius']
            bone.tail_radius = rec['tail_radius']
            bone.use_envelope_multiply = rec['use_envelope_multiply']
            for key, value in rec['properties'].items():
                bone[key] = value
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.update()
    for name, original_matrix in nonhair_before.items():
        if name not in export_armature.data.bones:
            raise RuntimeError('Nonhair bone unexpectedly removed: %s' % name)
        if _matrix_error(original_matrix, export_armature.data.bones[name].matrix_local) > 1e-5:
            raise RuntimeError('Nonhair rest matrix changed: %s' % name)
    errors = []
    for rec in records:
        actual = export_armature.data.bones[rec['name']].matrix_local
        error = _matrix_error(actual, rec['matrix'])
        if error > 1e-4:
            errors.append((rec['name'], error))
    if errors:
        raise RuntimeError('Grafted hair rest transform verification failed: %r' % errors[:10])
    return {'removed_donor_hair_bones': len(donor_names),
            'copied_port_hair_bones': len(records)}


def _subtree_names_from_edit(edit_bones, root_name):
    root = edit_bones.get(root_name)
    if root is None:
        return []
    out, queue = [], [root]
    while queue:
        bone = queue.pop(0)
        out.append(bone.name)
        queue.extend(bone.children)
    return out


def _copy_mesh_for_export(source, collection, armature):
    if source.type != 'MESH':
        raise TypeError('Explicit mesh lists must contain meshes: %s' % source.name)
    obj = source.copy()
    obj.data = source.data.copy()
    collection.objects.link(obj)
    # Blender .001 suffixes are normal; the exporter strips them.  Preserve
    # original mesh-data base names, scene flags, remap metadata, and shapes.
    world = source.matrix_world.copy()
    obj.parent = armature
    obj.matrix_parent_inverse.identity()
    obj.matrix_world = world
    mods = [mod for mod in obj.modifiers if mod.type == 'ARMATURE']
    if len(mods) > 1:
        raise ValueError('%s has multiple armature modifiers; resolve explicitly' % source.name)
    mod = mods[0] if mods else obj.modifiers.new(name='Armature', type='ARMATURE')
    mod.object = armature
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.hide_select = False
    return obj


def make_export_bundle(port_armature, clothing_armature, port_hair_meshes,
                       clothing_meshes, all_in_source_path,
                       name='Swerve_Port_ALL_IN_EXPORT', donor_collection=None):
    """Create an independent export rig and mesh copies; never edit originals.

    clothing_meshes must explicitly exclude ALL IN hair/cage meshes.  Include
    every desired gear render mesh and simulation cage.  port_hair_meshes must
    include every Port hair mesh the user wishes to keep, including facial hair
    where applicable; mesh names alone cannot reliably determine appearance.
    """
    _require_object_mode()
    if not os.path.isfile(all_in_source_path):
        raise FileNotFoundError(all_in_source_path)
    if not port_hair_meshes or not clothing_meshes:
        raise ValueError('Both explicit source mesh lists must be nonempty')
    if set(port_hair_meshes) & set(clothing_meshes):
        raise ValueError('Port hair and clothing mesh lists overlap')
    if name in bpy.data.collections:
        raise ValueError('Export collection already exists: %s' % name)
    collection = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(collection)
    if donor_collection is not None:
        _copy_id_properties(donor_collection, collection)
    collection['vcmodel_source_path'] = os.path.abspath(all_in_source_path)
    arm = clothing_armature.copy()
    arm.data = clothing_armature.data.copy()
    arm.name = name + '_Skeleton'
    collection.objects.link(arm)
    arm.hide_set(False)
    arm.hide_viewport = False
    arm.hide_select = False
    if 'J_Hair' in port_armature.data.bones:
        graft = graft_port_hair_subtree(port_armature, arm)
    else:
        # No hair bones on the base: the copied rig is already complete.
        graft = {'removed_donor_hair_bones': 0, 'copied_port_hair_bones': 0, 'skipped': 'Base rig has no J_Hair'}
    clothing = [_copy_mesh_for_export(obj, collection, arm) for obj in clothing_meshes]
    hair = [_copy_mesh_for_export(obj, collection, arm) for obj in port_hair_meshes]
    bpy.context.view_layer.update()
    return {'collection': collection, 'armature': arm, 'hair': hair,
            'clothing': clothing, 'meshes': clothing + hair, 'graft': graft}


def clean_material_name(name):
    # Same order as Link/interface_save.py:_clean_material_name.
    name = name.split(':', 1)[0]
    return re.sub(r'\.\d{3,}$', '', name)


def canonicalize_port_hair_materials(bundle):
    """Give export copies canonical Port hair material names without ambiguity.

    The addon queries bpy.data.materials by name, not the exact assigned slot.
    Duplicate donor hair materials can otherwise win lookup.  Here export hair
    gets independent material copies.  Conflicting unselected reference materials
    are renamed (no source files change).  A collision with an exported clothing
    material raises before any changes; deciding whether that clothing material
    should also be replaced or renamed requires visual inspection.
    """
    hair_materials = {}
    for obj in bundle['hair']:
        for slot in obj.material_slots:
            if slot.material:
                key = clean_material_name(slot.material.name)
                existing = hair_materials.get(key)
                if existing is not None and existing is not slot.material:
                    raise ValueError('Different Port hair materials share engine name %s' % key)
                hair_materials[key] = slot.material
    for obj in bundle['clothing']:
        for slot in obj.material_slots:
            mat = slot.material
            if mat and clean_material_name(mat.name) in hair_materials:
                wanted = hair_materials[clean_material_name(mat.name)]
                if mat is not wanted:
                    raise ValueError('Export clothing %s shares hair material name %s; resolve explicitly'
                                     % (obj.name, clean_material_name(mat.name)))
    replacements = {}
    report = {'renamed_references': [], 'canonical_export_materials': []}
    for key, source in hair_materials.items():
        clone = source.copy()
        # Rename conflicts globally so the addon's cleaned-name fallback cannot
        # find stale donor data.  Names include a nonnumeric suffix so cleaning
        # cannot collapse them back to the exported engine slot.
        conflicts = [m for m in bpy.data.materials if m is not clone
                     and clean_material_name(m.name).casefold() == key.casefold()]
        for index, other in enumerate(conflicts):
            old = other.name
            other.name = '%s_SOURCE_REFERENCE_%d' % (key, index)
            report['renamed_references'].append((old, other.name))
        clone.name = key + ':skinned'
        replacements[source] = clone
        report['canonical_export_materials'].append(clone.name)
    for obj in bundle['hair']:
        for slot in obj.material_slots:
            if slot.material in replacements:
                slot.material = replacements[slot.material]
    # When a clothing object intentionally shares the identical Port material,
    # point its copied slot to the same canonical clone too.
    for obj in bundle['clothing']:
        for slot in obj.material_slots:
            if slot.material in replacements:
                slot.material = replacements[slot.material]
    return report


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _copy_without_overwriting_different_file(source, target):
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists():
        if _sha256(source) != _sha256(target):
            raise FileExistsError('Refusing to overwrite different file: %s' % target)
    else:
        shutil.copy2(source, target)
    return {'source': str(source), 'target': str(target), 'sha256': _sha256(target)}


def prepare_hair_physics_directory(destination, port_basemodel_mcd,
                                   all_in_hair_ywa, all_in_hair_corr_ywa,
                                   basename='Swerve_HairTemplate'):
    """Prepare supported DIRECTORY donor mode with Port head/body colliders.

    Inputs are the actual ALL IN raw sidecar files already supplied by the user.
    They are copied without alteration; the exporter performs its own patching.
    No .ycl/.mtls/.ctg is guessed or copied.  A BaseModel MCD alone is sufficient
    for collider harvesting; do not put ALL IN BaseModel in this directory.
    """
    if not basename or any(c in basename for c in '/\\:'):
        raise ValueError('basename must be a simple filename stem')
    destination = Path(destination)
    inputs = [(port_basemodel_mcd, 'baseModel.mcd'),
              (all_in_hair_ywa, basename + '.hair_ywa'),
              (all_in_hair_corr_ywa, basename + '.hair_corr_ywa')]
    for source, _ in inputs:
        if not Path(source).is_file():
            raise FileNotFoundError(source)
        if Path(source).stat().st_size < 8:
            raise ValueError('Truncated donor file: %s' % source)
    destination.mkdir(parents=True, exist_ok=True)
    files = [_copy_without_overwriting_different_file(source, destination / name)
             for source, name in inputs]
    return {'directory': str(destination.resolve()), 'basename': basename, 'files': files}


def configure_hair_physics_swap(physics_directory, basename='Swerve_HairTemplate'):
    """Set only supported registered addon properties; leave clothing swap off."""
    scene = bpy.context.scene
    if not hasattr(scene, 'vcmodel_physics_swap'):
        raise RuntimeError('WWE2K_MDL physics properties are not registered')
    directory = Path(physics_directory)
    for name in ('baseModel.mcd', basename + '.hair_ywa', basename + '.hair_corr_ywa'):
        if not (directory / name).is_file():
            raise FileNotFoundError(directory / name)
    props = scene.vcmodel_physics_swap
    props.enabled = True
    props.source_mode = 'DIRECTORY'
    props.donor_directory = str(directory.resolve())
    props.donor_basename = basename
    props.donor_mcd_path = ''
    props.swap_hair = True
    props.swap_other = False
    return {'enabled': props.enabled, 'source_mode': props.source_mode,
            'donor_directory': props.donor_directory,
            'donor_basename': props.donor_basename,
            'swap_hair': props.swap_hair, 'swap_other': props.swap_other}


def select_export_bundle(bundle):
    _require_object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    for obj in bundle['meshes'] + [bundle['armature']]:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_select = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = bundle['armature']
    bpy.context.view_layer.update()


def validate_export_bundle(bundle, weight_threshold=1e-8,
                           ignored_weight_prefixes=('VC_GTPT__', 'HPL_')):
    """Return reviewable diagnostics and raise on unsafe export conditions."""
    errors, warnings, objects = [], [], []
    rig = bundle['armature']
    bone_names = set(rig.data.bones.keys())
    source = bundle['collection'].get('vcmodel_source_path')
    for obj in bundle['meshes']:
        base = re.sub(r'\.\d{3,}$', '', obj.data.name)
        if base in {'M_Head', 'M_Body', 'M_Head_Caruncle'}:
            errors.append('BaseModel mesh included in attire selection: ' + obj.name)
        mods = [m for m in obj.modifiers if m.type == 'ARMATURE']
        if len(mods) != 1 or mods[0].object is not rig:
            errors.append('Wrong armature modifier: ' + obj.name)
        if obj.parent is not rig:
            errors.append('Wrong parent: ' + obj.name)
        # The exporter resolves source by collection and selection order.
        stamped = {col.get('vcmodel_source_path') for col in obj.users_collection
                   if col.get('vcmodel_source_path')}
        if stamped != {source}:
            errors.append('Ambiguous source collections on %s: %r' % (obj.name, stamped))
        group_names = {g.index: g.name for g in obj.vertex_groups}
        missing = set()
        for vertex in obj.data.vertices:
            for group in vertex.groups:
                if group.weight > weight_threshold:
                    name = group_names.get(group.group)
                    if name and name.startswith(ignored_weight_prefixes):
                        continue  # known annotation groups, not skin weights
                    if name not in bone_names:
                        missing.add(name)
        if missing:
            errors.append('Weighted groups without rig bones on %s: %r' % (obj.name, sorted(missing)))
        if not obj.data.polygons:
            warnings.append('Polygon-less mesh: exporter may skip %s' % obj.name)
        objects.append({'object': obj.name, 'mesh': obj.data.name,
                        'vertices': len(obj.data.vertices),
                        'polygons': len(obj.data.polygons),
                        'materials': [slot.material.name if slot.material else None
                                      for slot in obj.material_slots]})
    if errors:
        raise RuntimeError('Unsafe export bundle:\n' + '\n'.join(errors))
    return {'source_path': source, 'armature': rig.name, 'bones': len(rig.data.bones),
            'objects': objects, 'warnings': warnings}
