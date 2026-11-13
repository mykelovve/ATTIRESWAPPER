"""Synthetic regression test for the bounded garment fit. Needs no game files.

Run (Blender 4.3+):
  blender --background --factory-startup --python tests/blender_fit_test.py -- Source
Exits non-zero on failure.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(sys.argv[sys.argv.index('--') + 1]).resolve()))
from garment_fit import fit_garments  # noqa: E402
from fit_helpers import _build_body_bvh, _nearest  # noqa: E402

RADIUS = 20.0
LIMIT = 2.0


def sphere(name, radius, segments=48, rings=24):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=segments, ring_count=rings)
    obj = bpy.context.active_object
    obj.name = obj.data.name = name
    return obj


def depth_inside(bvh, point):
    return -_nearest(bvh, point)[2]


def run():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    body = sphere('Body', RADIUS)
    body_before = [v.co.copy() for v in body.data.vertices]
    bvh, _ = _build_body_bvh(body)
    failures = []

    def check(condition, message):
        print(('PASS ' if condition else 'FAIL ') + message)
        if not condition:
            failures.append(message)

    # 1. Shallow penetration is fully repaired.
    shallow = sphere('Shallow', RADIUS - .8)
    item = fit_garments([body], [shallow])['objects'][0]
    world = [shallow.matrix_world @ v.co for v in shallow.data.vertices]
    check(item['fully_resolved'], 'shallow garment fully resolved')
    check(max(depth_inside(bvh, p) for p in world) <= 1e-3, 'shallow garment has no vertex inside')

    # 2. One deep spike no longer vetoes the garment; travel stays bounded.
    spiky = sphere('Spiky', RADIUS - .8)
    original = [v.co.copy() for v in spiky.data.vertices]
    spike = max(range(len(original)), key=lambda i: original[i].z)
    spiky.data.vertices[spike].co = Vector((0, 0, RADIUS - 9))
    original[spike] = spiky.data.vertices[spike].co.copy()
    item = fit_garments([body], [spiky])['objects'][0]
    travel = [(v.co - o).length for v, o in zip(spiky.data.vertices, original)]
    check(not item['fully_resolved'] and not item['interior_item'], 'spiky garment reported as partial')
    check(max(travel) <= LIMIT + 1e-4, 'no vertex travels beyond the %.1f cm limit (max %.3f)' % (LIMIT, max(travel)))
    check(travel[spike] <= 1e-6, 'deep spike vertex left in place')
    healthy = [i for i in range(len(original)) if (original[i] - original[spike]).length > 6]
    still_inside = sum(1 for i in healthy if depth_inside(bvh, spiky.matrix_world @ spiky.data.vertices[i].co) > 1e-3)
    check(still_inside == 0, 'rest of the spiky garment repaired (%d healthy vertices still inside)' % still_inside)

    # 3. Interior items (mouthguard-like) are left alone.
    inner = sphere('Inner', RADIUS - 6, segments=16, rings=8)
    inner_before = [v.co.copy() for v in inner.data.vertices]
    item = fit_garments([body], [inner])['objects'][0]
    check(item['interior_item'], 'deep interior item detected')
    check(all((v.co - o).length == 0 for v, o in zip(inner.data.vertices, inner_before)), 'interior item unchanged')

    # 4. Shape keys are kept: each key's offset from Basis is unchanged.
    keyed = sphere('Keyed', RADIUS - .8)
    keyed.shape_key_add(name='Basis')
    key = keyed.shape_key_add(name='Flex')
    for point in key.data:
        point.co = point.co * 1.05 + Vector((0, 0, .5))
    blocks = keyed.data.shape_keys.key_blocks
    delta_before = [(k.co - b.co).copy() for k, b in zip(blocks['Flex'].data, blocks['Basis'].data)]
    item = fit_garments([body], [keyed])['objects'][0]
    delta_after = [(k.co - b.co) for k, b in zip(blocks['Flex'].data, blocks['Basis'].data)]
    drift = max((a - b).length for a, b in zip(delta_before, delta_after))
    moved = max((v.co - b.co).length for v, b in zip(keyed.data.vertices, blocks['Basis'].data))
    check(item['shape_keys_offset_with_basis'], 'shape-keyed garment fitted instead of refused')
    check(drift <= 1e-4, 'shape key offsets preserved (drift %.2e)' % drift)
    check(moved <= 1e-4, 'mesh and Basis key agree after fit')

    check(all((v.co - o).length == 0 for v, o in zip(body.data.vertices, body_before)), 'body geometry unchanged')
    print('RESULT %s (%d failure%s)' % ('OK' if not failures else 'FAILED', len(failures), '' if len(failures) == 1 else 's'))
    return not failures


if __name__ == '__main__':
    ok = False
    try:
        ok = run()
    finally:
        sys.stdout.flush()
        if not ok:
            import os
            os._exit(1)
