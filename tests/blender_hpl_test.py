"""Synthetic body-mask safety regressions; no game models or native DLL needed.

Run: blender --background --factory-startup --python-exit-code 1
     --python tests/blender_hpl_test.py -- Source
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch

import bpy
from bpy.props import IntProperty, PointerProperty
from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(sys.argv[sys.argv.index('--') + 1]).resolve()))
import swap_worker as worker


class MaskProperties(bpy.types.PropertyGroup):
    appearance_flag: IntProperty(default=128)


bpy.utils.register_class(MaskProperties)
bpy.types.Object.mdl_properties = PointerProperty(type=MaskProperties)


def reset():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def material(name='Opaque', alpha=1.0, image_alpha=None):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(node for node in mat.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
    bsdf.inputs['Alpha'].default_value = alpha
    if image_alpha is not None:
        image = bpy.data.images.new(name + '_RGBA', width=4, height=4, alpha=True)
        pixels = [1.0, 1.0, 1.0, 1.0] * 16
        pixels[3] = image_alpha
        image.pixels = pixels
        image.update()
        image.alpha_mode = 'STRAIGHT'
        node = mat.node_tree.nodes.new('ShaderNodeTexImage')
        node.image = image
        mat.node_tree.links.new(node.outputs['Color'], bsdf.inputs['Base Color'])
        mat.node_tree.links.new(node.outputs['Alpha'], bsdf.inputs['Alpha'])
    return mat


def mesh(name, points, faces, mats=None):
    data = bpy.data.meshes.new(name)
    data.from_pydata(points, [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    for mat in mats or [material()]:
        data.materials.append(mat)
    return obj


def grid(name='M_Body', size=7, z=0):
    points = [(x, y, z) for y in range(size) for x in range(size)]
    faces = [(y*size+x, y*size+x+1, (y+1)*size+x+1, (y+1)*size+x)
             for y in range(size-1) for x in range(size-1)]
    return mesh(name, points, faces)


def panel(name='M_Top', z=.5, mat=None):
    return mesh(name, [(-100, -100, z), (100, -100, z), (100, 100, z), (-100, 100, z)],
                [(0, 1, 2, 3)], [mat or material()])


def mask_count(result, body, scene='HPL_Ring'):
    return result[worker.clean_mesh(body.data.name)]['masked'].get(scene, 0)


def signature(body):
    return ([tuple(v.co) for v in body.data.vertices],
            [tuple(p.vertices) for p in body.data.polygons])


def run():
    failures, checks = [], 0
    def check(condition, name):
        nonlocal checks
        checks += 1
        print(('PASS ' if condition else 'FAIL ') + name)
        if not condition:
            failures.append(name)

    # Native HPL decoding is stubbed only to observe whether the reuse gate
    # calls it. Coverage tests below use real Blender BVHs and material graphs.
    stub = types.ModuleType('WWE2K_MDL.Serialize.hpl_roundtrip')
    calls = []
    stub.extract_hpl_from_mcd = lambda path: calls.append('extract') or bytes(64)
    stub.apply_hpl_to_basemodel = lambda data: (True, 'fixture mask applied')
    sys.modules['WWE2K_MDL.Serialize.hpl_roundtrip'] = stub
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        base_path, donor_path = root/'base.mcd', root/'donor.mcd'
        base_path.write_bytes(b'original native vertex order')
        donor_path.write_bytes(b'different native vertex order')
        reset()
        body, head = grid(size=3), grid('M_Head', size=2)
        counts = worker._body_counts([body, head])
        info = {'path': str(donor_path), 'sha256': worker.sha256(donor_path)}
        job = {'base_model': base_path, 'donor_attire': root/'Attire.mcd',
               '_donor_base_model_identity': info}
        result = worker._apply_donor_hpl(job, {'meshes': [body, head]}, counts, [])
        check(not result['applied'] and not calls,
              'equal vertex counts cannot reuse masks from a different native BaseModel')
        donor_path.write_bytes(base_path.read_bytes())
        info['sha256'] = worker.sha256(donor_path)
        result = worker._apply_donor_hpl(job, {'meshes': [body, head]}, counts, [])
        check(result['applied'] and calls == ['extract'],
              'byte-identical BaseModel can reuse authored donor masks')
        calls.clear()
        donor_path.write_bytes(b'changed while importing')
        result = worker._apply_donor_hpl(job, {'meshes': [body, head]}, counts, [])
        check(not result['applied'] and not calls,
              'changed donor model invalidates the captured identity')

    reset()
    body = grid()
    before = signature(body)
    clothing = panel()
    result = worker._generate_hpl({'meshes': [body]}, [clothing])
    check(mask_count(result, body) == 49 and mask_count(result, body, 'HPL_Entrance') == 49,
          'opaque full coverage still generates both scene masks')
    check(signature(body) == before, 'mask generation preserves body geometry and topology')

    reset()
    body = grid()
    points, faces = [], []
    for vertex in body.data.vertices:
        x, y, _ = vertex.co
        first = len(points)
        points.extend([(x-.03, y-.03, .5), (x+.03, y-.03, .5),
                       (x+.03, y+.03, .5), (x-.03, y+.03, .5)])
        faces.append(tuple(first+i for i in range(4)))
    tiles = mesh('M_CutoutTiles', points, faces)
    result = worker._generate_hpl({'meshes': [body]}, [tiles])
    detail = result[worker.clean_mesh(body.data.name)]['face_coverage']['HPL_Ring']
    check(detail['vertex_hits'] == 49, 'sparse tiles really cover every body vertex ray')
    check(mask_count(result, body) == 0 and detail['sampled_triangles_covered'] == 0,
          'vertex hits cannot hide exposed face interiors between cloth tiles')

    for flag, ring, entrance in [(129, 0, 49), (130, 49, 0)]:
        reset()
        body, clothing = grid(), panel()
        clothing.mdl_properties.appearance_flag = flag
        result = worker._generate_hpl({'meshes': [body]}, [clothing])
        check(mask_count(result, body) == ring and mask_count(result, body, 'HPL_Entrance') == entrance,
              'scene appearance flag %d keeps Ring and Entrance independent' % flag)

    reset()
    head, mouthguard = grid('M_Head'), panel('M_Mouthguard', z=-1)
    diagnostics = {}
    result = worker._generate_hpl({'meshes': [head]}, [mouthguard], diagnostics=diagnostics)
    check(mask_count(result, head) == 0 and diagnostics['skipped_objects'][0]['reason'] == 'interior_accessory',
          'internal mouthguard does not remove exposed head skin')

    reset()
    body, lining = grid(), panel('M_InteriorInsert', z=-1)
    warnings = []
    result = worker._body_hide_masks({}, {'meshes': [body]}, [lining], None, warnings,
                                    fit_report={'objects': [{'mesh': 'M_InteriorInsert',
                                                           'status': 'interior_item_unchanged'}]})
    check(not result['applied'] and result['generated_mask_diagnostics']['skipped_objects'],
          'fit-reported interior objects are excluded without name guessing')

    for name, mat in [('scalar alpha', material(alpha=.25)),
                      ('texture alpha', material(image_alpha=0.0)),
                      ('explicit alpha metadata', material()),
                      ('native alpha slot', material('glasses_ALPHA'))]:
        if name == 'explicit alpha metadata':
            mat['vc_2k26_alpha'] = True
        reset()
        body, clothing = grid(), panel(mat=mat)
        diagnostics = {}
        result = worker._generate_hpl({'meshes': [body]}, [clothing], diagnostics=diagnostics)
        check(mask_count(result, body) == 0 and diagnostics['skipped_material_faces'],
              name + ' does not act as opaque body coverage')

    reset()
    body, clothing = grid(), panel(mat=material(image_alpha=1.0))
    result = worker._generate_hpl({'meshes': [body]}, [clothing])
    check(mask_count(result, body) == 49,
          'fully opaque RGBA texture is eligible despite having an alpha channel')

    reset()
    body = grid()
    mixed = mesh('M_Mixed', [(-1, -1, .5), (3.5, -1, .5), (3.5, 7, .5), (-1, 7, .5),
                            (7, -1, .5), (7, 7, .5)],
                 [(0, 1, 2, 3), (1, 4, 5, 2)], [material(), material(alpha=0.0)])
    mixed.data.polygons[1].material_index = 1
    result = worker._generate_hpl({'meshes': [body]}, [mixed])
    group = body.vertex_groups.get('HPL_Ring')
    hidden = [v.index for v in body.data.vertices if any(g.group == group.index for g in v.groups)] if group else []
    check(bool(hidden) and all(body.data.vertices[i].co.x <= 2 for i in hidden),
          'mixed opaque/transparent mesh masks only safely covered opaque-side vertices')

    reset()
    body, cage = grid(), panel('S_Jacket')
    result = worker._generate_hpl({'meshes': [body]}, [cage])
    check(mask_count(result, body) == 0, 'simulation cages are never visible mask occluders')

    reset()
    body = grid()
    for name in ('HPL_Ring', 'HPL_Entrance'):
        body.vertex_groups.new(name=name).add(list(range(49)), 1.0, 'REPLACE')
    result = worker._generate_hpl({'meshes': [body]}, [])
    check(not any(v.groups for v in body.data.vertices),
          'recomputing with no clothing clears stale masks in both scenes')

    def authored_all(job, base, counts, warnings):
        for obj in base['meshes']:
            for name in ('HPL_Ring', 'HPL_Entrance'):
                obj.vertex_groups.new(name=name).add(list(range(len(obj.data.vertices))), 1.0, 'REPLACE')
        return {'applied': True}

    reset()
    body = grid()
    strap = mesh('M_Strap', [(-1, -1, .1), (3.5, -1, .1), (3.5, 7, .1), (-1, 7, .1)],
                 [(0, 1, 2, 3)])
    with patch.object(worker, '_apply_donor_hpl', side_effect=authored_all):
        result = worker._body_hide_masks({}, {'meshes': [body]}, [strap], None, [])
    group = body.vertex_groups['HPL_Ring']
    hidden = [v.index for v in body.data.vertices if any(g.group == group.index for g in v.groups)]
    check(bool(hidden) and all(body.data.vertices[i].co.x <= 2 for i in hidden)
          and any(v['uncovered_authored_vertices_removed'] for v in result['authored_mask_coverage_validation']),
          'authored masks cannot leave holes outside the current strap coverage')

    reset()
    body, head, clothing = grid(), grid('M_Head'), panel()
    for vertex in head.data.vertices:
        vertex.co.x += 1000
    with patch.object(worker, '_apply_donor_hpl', side_effect=authored_all):
        result = worker._body_hide_masks({}, {'meshes': [body, head]}, [clothing], None, [])
    check(mask_count(result['generated'], body) == 49 and mask_count(result['generated'], head) == 0
          and not any(v.groups for v in head.data.vertices),
          'donor head masks cannot hide skin where retained outfit has no coverage')

    reset()
    body = grid()
    def partial_failure(job, base, counts, warnings):
        authored_all(job, base, counts, warnings)
        return {'applied': False, 'reason': 'synthetic interrupted mask import'}
    with patch.object(worker, '_apply_donor_hpl', side_effect=partial_failure):
        result = worker._body_hide_masks({}, {'meshes': [body]}, [], None, [])
    check(not result['applied'] and not any(v.groups for v in body.data.vertices),
          'partially failed authored import cannot retain stale hidden skin')

    reset()
    unloaded = bpy.data.materials.new('NativeMapNotLoaded')
    unloaded['vc_2k26_color_map_hash'] = 123
    body, clothing = grid(), panel(mat=unloaded)
    diagnostics = {}
    result = worker._generate_hpl({'meshes': [body]}, [clothing], diagnostics=diagnostics)
    check(mask_count(result, body) == 0
          and diagnostics['skipped_material_faces'][0]['reason'] == 'unloaded_color_image_opacity',
          'an unloaded native texture cannot be guessed opaque from its placeholder material')

    reset()
    bpy.ops.mesh.primitive_cube_add(size=2)
    cube = bpy.context.active_object
    cube.data.calc_loop_triangles()
    occluder = BVHTree.FromPolygons([v.co for v in cube.data.vertices],
                                   [tuple(t.vertices) for t in cube.data.loop_triangles], all_triangles=True)
    cloth = panel(z=-2)
    cloth.data.calc_loop_triangles()
    cloth_tree = BVHTree.FromPolygons([v.co for v in cloth.data.vertices],
                                     [tuple(t.vertices) for t in cloth.data.loop_triangles], all_triangles=True)
    point, normal = Vector((0, 0, 1)), Vector((0, 0, 1))
    check(not worker._fabric_behind(cloth_tree, occluder, point, normal)
          and not worker._skin_poking_out(cloth_tree, occluder, point, normal),
          'Claude far-side limb guard remains intact for inward and nearest-surface coverage')

    beard = panel('M_Combinations')
    check(worker._classify_mesh(beard) == 'hair',
          'Claude donor-beard exclusion remains intact')

    reset()
    hair, stray, driven_cage, wrist = (panel('S_Hair'), panel('S_Jacket'),
                                      panel('S_ClothTail'), panel('M_Wrist'))
    stray_name = stray.name
    hair_before = signature(hair)
    bundle = {'hair': [hair], 'clothing': [stray, driven_cage, wrist],
              'meshes': [hair, stray, driven_cage, wrist]}
    dropped = worker._drop_undriven_clothing_cages(bundle, {'S_Hair', 'S_ClothTail'})
    check(dropped == ['S_Jacket'] and bpy.data.objects.get(stray_name) is None,
          'Callum-style undriven S_Jacket cannot be exported as a black band')
    check(bundle['clothing'] == [driven_cage, wrist] and wrist in bundle['meshes'],
          'real wrist attire and driven clothing cages remain in the export')
    check(hair in bundle['meshes'] and signature(hair) == hair_before,
          'stray-cage removal does not touch the preserved base hair cage')

    result = {'passed': not failures, 'checks': checks, 'failures': failures,
              'blender': bpy.app.version_string,
              'limits': 'Synthetic rest-pose decision tests; no game or live-physics validation.'}
    print('HPL_TEST_RESULT ' + json.dumps(result))
    return not failures


if __name__ == '__main__':
    assert run(), 'Body-mask safety regressions failed'
