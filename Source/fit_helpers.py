"""Conservative rest-pose clothing fitting against an explicit body mesh.

Coordinates in this project are centimetres (the source character is ~196 units
tall).  The default moves only clothing vertices initially inside the body;
opt-in surface constraints also correct corners of penetrating coarse faces.
Neither mode edits the body, UVs, polygon indices, skin weights, or materials.

No vertex ever travels further than ``max_displacement``.  Vertices that would
need more are tapered or left alone and reported, instead of vetoing the whole
garment: hidden hems, caps and tucked-in geometry routinely sit far inside.

Nearest-surface signed distance is suitable for the leg surfaces in this task;
it is not a proof of solid containment for an open or self-intersecting mesh.
The report therefore includes midpoint/centroid checks in addition to vertices.
"""

from __future__ import annotations

import hashlib
import math
import struct

from mathutils import Vector
from mathutils.bvhtree import BVHTree


class FitLimitError(RuntimeError):
    """No geometry was changed; inspect ``report`` before choosing another fit."""

    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def _require_mesh(obj, role):
    if getattr(obj, "type", None) != "MESH":
        raise TypeError("%s must be an explicit mesh object" % role)
    if obj.mode != 'OBJECT':
        raise ValueError("%s must be in Object mode" % obj.name)


def _position_digest(obj):
    h = hashlib.sha256()
    for vertex in obj.data.vertices:
        h.update(struct.pack('<3f', *vertex.co))
    return h.hexdigest()


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))]


def _build_body_bvh(body):
    _require_mesh(body, 'body')
    vertices = [body.matrix_world @ v.co for v in body.data.vertices]
    body.data.calc_loop_triangles()
    triangles = [tuple(t.vertices) for t in body.data.loop_triangles]
    if not vertices or not triangles:
        raise ValueError('Body has no triangulated surface')
    center = sum(vertices, Vector()) / len(vertices)
    signed_volume = sum((vertices[a] - center).dot(
        (vertices[b] - center).cross(vertices[c] - center))
        for a, b, c in triangles) / 6.0
    # A reflection reverses triangle winding; volume also catches this without
    # relying on how object and mesh transforms divide the reflected axes.
    reversed_normals = signed_volume < 0.0
    if reversed_normals:
        triangles = [(a, c, b) for a, b, c in triangles]
    bvh = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)
    return bvh, {'signed_volume_before_orientation': signed_volume,
                 'reversed_bvh_normals': reversed_normals,
                 'vertices': len(vertices), 'triangles': len(triangles)}


def _nearest(bvh, point):
    location, normal, face, distance = bvh.find_nearest(point)
    if location is None or normal is None or normal.length_squared < 0.5:
        raise RuntimeError('No valid nearest body surface')
    normal = normal.normalized()
    signed = (point - location).dot(normal)
    return location, normal, float(signed), float(distance)


def _summary(bvh, positions, tolerance):
    signed = [_nearest(bvh, point)[2] for point in positions]
    penetrations = [-d for d in signed if d < -tolerance]
    return {'vertices': len(positions),
            'inside_vertices': len(penetrations),
            'deepest_penetration': max(penetrations, default=0.0),
            'signed_distance_min': min(signed, default=None),
            'signed_distance_p05': _percentile(signed, .05),
            'signed_distance_median': _percentile(signed, .5)}


def _surface_constraints(obj):
    constraints = [(tuple(e.vertices), (.5, .5)) for e in obj.data.edges]
    for polygon in obj.data.polygons:
        count = len(polygon.vertices)
        constraints.append((tuple(polygon.vertices), (1.0 / count,) * count))
    return constraints


def _sample_point(indices, weights, positions):
    return sum((positions[i] * w for i, w in zip(indices, weights)), Vector())


def _surface_samples(bvh, constraints, positions, tolerance):
    # Keep this diagnostic bounded; each geometric edge is sampled once and
    # polygon centroids catch triangles spanning a convex body surface.
    depths = [-_nearest(bvh, _sample_point(indices, weights, positions))[2]
              for indices, weights in constraints]
    inside = [d for d in depths if d > tolerance]
    return {'samples': len(constraints), 'inside_samples': len(inside),
            'deepest_sample_penetration': max(inside, default=0.0)}


def _clamp_travel(positions, original, index, limit):
    """Keep one vertex inside the allowed travel ball around its source."""
    offset = positions[index] - original[index]
    length = offset.length
    if length > limit:
        positions[index] = original[index] + offset * (limit / length)


def _allowed_travel(depth, clearance, limit):
    """Travel budget for a vertex ``depth`` cm below the body surface.

    ``fix``    it can reach ``clearance`` outside within the limit.
    ``taper``  it cannot; it moves a shrinking distance so the garment stays
               continuous between repaired and untouched regions.
    ``deep``   twice the limit or deeper: intentional interior geometry or a
               mismatch too large for an automatic fit. It is not moved.
    """
    needed = depth + clearance
    if needed <= limit:
        return needed, 'fix'
    if depth >= 2.0 * limit:
        return 0.0, 'deep'
    return limit * (2.0 * limit - depth) / (limit + clearance), 'taper'


def _project_surface_constraints(bvh, constraints, positions, original, frozen,
                                 iterations, surface_clearance, tolerance, limit):
    """Position-based correction of sampled edge/face penetration.

    Unlike the initial pass this is explicitly allowed to move vertices that
    started outside: a coarse triangle can cross a convex body even when all
    its corners are outside.  Each correction uses the minimum squared-motion
    distribution for the sample's barycentric weights.

    Samples that touch a frozen (tapered/deep) vertex, or that are themselves
    deeper than the travel limit, cannot be resolved by a bounded move.  They
    are skipped: correcting them is what used to drag healthy neighbours across
    the body.  Every moved vertex is clamped to the travel limit.
    """
    usable = [(indices, weights) for indices, weights in constraints
              if not any(index in frozen for index in indices)]
    passes = []
    for _ in range(iterations):
        corrected = 0
        too_deep = 0
        max_correction = 0.0
        for indices, weights in usable:
            point = _sample_point(indices, weights, positions)
            _, normal, signed, _ = _nearest(bvh, point)
            if signed >= surface_clearance - tolerance:
                continue
            if -signed > limit:
                too_deep += 1
                continue
            amount = surface_clearance - signed
            denominator = sum(weight * weight for weight in weights)
            for index, weight in zip(indices, weights):
                positions[index] += normal * (amount * weight / denominator)
                _clamp_travel(positions, original, index, limit)
            corrected += 1
            max_correction = max(max_correction, amount)
        # A face correction can cross a nearby fold; keep all vertices outside
        # without broadening to a shrinkwrap of vertices that are already safe.
        vertices_corrected = 0
        for index, point in enumerate(positions):
            if index in frozen:
                continue
            location, normal, signed, _ = _nearest(bvh, point)
            if signed < -tolerance:
                positions[index] = location + normal * surface_clearance
                _clamp_travel(positions, original, index, limit)
                vertices_corrected += 1
        passes.append({'corrected_samples': corrected,
                       'corrected_vertices': vertices_corrected,
                       'skipped_too_deep_samples': too_deep,
                       'max_sample_correction_cm': max_correction})
        if not corrected and not vertices_corrected:
            break
    return passes


def fit_clothing_to_body(body, clothing_objects, *, clearance=.1,
                         max_displacement=3.0, penetration_tolerance=1e-4,
                         max_iterations=4, surface_iterations=0,
                         surface_clearance=.03, dry_run=False, strict=False,
                         surface_constraints=None, interior_fraction=.8):
    """Project initially-inside clothing vertices just outside the body.

    All proposals are computed before writing any coordinate, and no vertex
    travels more than ``max_displacement``.  A vertex that would need more is
    tapered or left in place (see ``_allowed_travel``) and counted in the
    report; the rest of the garment is still repaired.  Each object reports
    ``fully_resolved`` so callers can tell a complete fit from a partial one.

    A garment whose vertices are mostly inside the body, deeper than the limit
    (a mouthguard, an inner lining), is an interior item, not a fitting error.
    It is reported with ``interior_item`` and left unchanged.

    ``strict=True`` restores the all-or-nothing contract: any object that is
    not fully resolved raises :class:`FitLimitError` and nothing is written.
    Existing shape keys beyond Basis are refused here: callers that handle
    them must fit an independent copy and transfer the offsets themselves.
    Shared mesh datablocks are also refused.

    ``dry_run=True`` returns the same proposed metrics without applying them.
    With the default ``surface_iterations=0``, surface sample penetrations are
    reported without moving originally-outside vertices.  Opting into surface
    iterations permits minimal edge/centroid constraint corrections on their
    vertices too.  ``surface_constraints`` may supply a denser sampler.  This
    is still a sampled rest-pose check, not an animation or full continuous
    collision guarantee.
    """
    if not (0 <= clearance <= max_displacement and math.isfinite(max_displacement)
            and max_displacement > 0 and 1 <= max_iterations <= 20):
        raise ValueError('Invalid clearance, displacement limit, or iteration count')
    if not (0 <= surface_iterations <= 30 and 0 <= surface_clearance <= max_displacement):
        raise ValueError('Invalid surface constraint options')
    objects = list(clothing_objects)
    if not objects or len(set(objects)) != len(objects):
        raise ValueError('Pass a nonempty list of distinct clothing objects')
    sampler = surface_constraints or _surface_constraints
    body_digest = _position_digest(body)
    bvh, body_info = _build_body_bvh(body)
    report = {'body': body.name, 'body_sha256': body_digest,
              'body_surface': body_info, 'clearance_cm': clearance,
              'max_displacement_cm': max_displacement,
              'surface_iterations': surface_iterations,
              'surface_clearance_cm': surface_clearance,
              'applied': False, 'objects': []}
    proposals = []
    incomplete = []
    for obj in objects:
        _require_mesh(obj, 'clothing')
        if obj is body or obj.data is body.data:
            raise ValueError('Clothing cannot share the body datablock')
        if obj.data.users > 1:
            raise ValueError('%s has shared mesh data; fit an independent export copy' % obj.name)
        keys = obj.data.shape_keys
        if keys is not None and len(keys.key_blocks) > 1:
            raise ValueError('%s has non-Basis shape keys; fitting requires explicit target handling' % obj.name)
        original = [obj.matrix_world @ v.co for v in obj.data.vertices]
        fitted = [p.copy() for p in original]
        constraints = sampler(obj)
        nearest = [_nearest(bvh, point) for point in original]
        inside_indices = [i for i, item in enumerate(nearest)
                          if item[2] < -penetration_tolerance]
        depths = sorted(-nearest[i][2] for i in inside_indices)
        before = _summary(bvh, original, penetration_tolerance)
        samples_before = _surface_samples(bvh, constraints, original, penetration_tolerance)
        interior = bool(original) and (
            len(inside_indices) >= interior_fraction * len(original)
            and depths[len(depths) // 2] > max_displacement)
        frozen = set()
        tapered = deep = 0
        surface_passes = []
        if not interior:
            fixable = []
            for index in inside_indices:
                location, normal, signed, _ = nearest[index]
                target = location + normal * clearance
                travel, kind = _allowed_travel(-signed, clearance, max_displacement)
                if kind == 'fix':
                    fitted[index] = target
                    # Near body edges the nearest point is not straight along the
                    # normal, so the real move can exceed the signed depth.
                    _clamp_travel(fitted, original, index, max_displacement)
                    fixable.append(index)
                    continue
                frozen.add(index)
                if kind == 'deep':
                    deep += 1
                    continue
                tapered += 1
                offset = target - original[index]
                if offset.length > 1e-9:
                    fitted[index] = original[index] + offset * (travel / offset.length)
            for _ in range(max_iterations):
                remaining = 0
                for index in fixable:
                    location, normal, signed, _ = _nearest(bvh, fitted[index])
                    if signed < clearance - penetration_tolerance:
                        fitted[index] = location + normal * clearance
                        _clamp_travel(fitted, original, index, max_displacement)
                        remaining += 1
                if remaining == 0:
                    break
            if surface_iterations:
                surface_passes = _project_surface_constraints(
                    bvh, constraints, fitted, original, frozen, surface_iterations,
                    surface_clearance, penetration_tolerance, max_displacement)
        moved_indices = [i for i in range(len(fitted))
                         if (fitted[i] - original[i]).length > 1e-7]
        travels = [(fitted[i] - original[i]).length for i in moved_indices]
        # Blender vectors are 32-bit floats: allow rounding at ~200 cm coordinates.
        if max(travels, default=0.0) > max_displacement + 1e-3:
            raise RuntimeError('Internal error: a fitted vertex exceeded the travel limit')
        after = _summary(bvh, fitted, penetration_tolerance)
        samples_after = _surface_samples(bvh, constraints, fitted, penetration_tolerance)
        resolved = not interior and not after['inside_vertices'] and not (
            surface_iterations and samples_after['inside_samples'])
        item = {'object': obj.name, 'mesh': obj.data.name,
                'initially_inside_vertices': len(inside_indices),
                'changed_vertices': len(moved_indices),
                'outside_vertices_adjusted_for_surface_constraints': len(
                    set(moved_indices) - set(inside_indices)),
                'surface_constraint_passes': surface_passes,
                'max_travel_cm': max(travels, default=0.0),
                'travel_p95_cm': _percentile(travels, .95),
                'tapered_vertices': tapered,
                'deep_interior_vertices': deep,
                'unresolved_inside_vertices': after['inside_vertices'],
                'deepest_remaining_penetration_cm': after['deepest_penetration'],
                'interior_item': interior,
                'fully_resolved': resolved,
                'before': before, 'after': after,
                'surface_samples_before': samples_before,
                'surface_samples_after': samples_after}
        report['objects'].append(item)
        proposals.append((obj, moved_indices, fitted))
        if not resolved:
            incomplete.append(obj.name)
    if strict and incomplete:
        raise FitLimitError('Clothing fit is incomplete within the displacement limit: '
                            + ', '.join(incomplete), report)
    if not dry_run:
        for obj, indices, positions in proposals:
            inverse = obj.matrix_world.inverted()
            basis = obj.data.shape_keys.key_blocks[0] if obj.data.shape_keys else None
            for index in indices:
                coordinate = inverse @ positions[index]
                obj.data.vertices[index].co = coordinate
                if basis is not None:
                    basis.data[index].co = coordinate
            obj.data.update()
        report['applied'] = True
    if _position_digest(body) != body_digest:
        raise RuntimeError('Body geometry unexpectedly changed during fitting')
    return report


# Descriptive alias for callers that want the restricted edit policy in the name.
fit_inside_vertices = fit_clothing_to_body
