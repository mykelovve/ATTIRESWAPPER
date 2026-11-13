"""Conservative rest-pose clothing fitting against an explicit body mesh.

Coordinates in this project are centimetres (the source character is ~196 units
tall).  The default moves only clothing vertices initially inside the body;
opt-in surface constraints also correct corners of penetrating coarse faces.
Neither mode edits the body, UVs, polygon indices, skin weights, or materials.

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


def _surface_samples(bvh, obj, positions, tolerance):
    # Keep this diagnostic bounded; each geometric edge is sampled once and
    # polygon centroids catch triangles spanning a convex body surface.
    constraints = _surface_constraints(obj)
    depths = [-_nearest(bvh, _sample_point(indices, weights, positions))[2]
              for indices, weights in constraints]
    inside = [d for d in depths if d > tolerance]
    return {'samples': len(constraints), 'inside_samples': len(inside),
            'deepest_sample_penetration': max(inside, default=0.0)}


def _project_surface_constraints(bvh, obj, positions, iterations,
                                 surface_clearance, tolerance):
    """Position-based correction of sampled edge/face penetration.

    Unlike the initial pass this is explicitly allowed to move vertices that
    started outside: a coarse triangle can cross a convex body even when all
    its corners are outside.  Each correction uses the minimum squared-motion
    distribution for the sample's barycentric weights.  Global travel limits
    are checked by the caller before any Blender coordinates are written.
    """
    constraints = _surface_constraints(obj)
    passes = []
    for _ in range(iterations):
        corrected = 0
        max_correction = 0.0
        for indices, weights in constraints:
            point = _sample_point(indices, weights, positions)
            _, normal, signed, _ = _nearest(bvh, point)
            if signed >= surface_clearance - tolerance:
                continue
            amount = surface_clearance - signed
            denominator = sum(weight * weight for weight in weights)
            for index, weight in zip(indices, weights):
                positions[index] += normal * (amount * weight / denominator)
            corrected += 1
            max_correction = max(max_correction, amount)
        # A face correction can cross a nearby fold; keep all vertices outside
        # without broadening to a shrinkwrap of vertices that are already safe.
        vertices_corrected = 0
        for index, point in enumerate(positions):
            location, normal, signed, _ = _nearest(bvh, point)
            if signed < -tolerance:
                positions[index] = location + normal * surface_clearance
                vertices_corrected += 1
        passes.append({'corrected_samples': corrected,
                       'corrected_vertices': vertices_corrected,
                       'max_sample_correction_cm': max_correction})
        if not corrected and not vertices_corrected:
            break
    return passes


def fit_clothing_to_body(body, clothing_objects, *, clearance=.1,
                         max_displacement=3.0, penetration_tolerance=1e-4,
                         max_iterations=4, surface_iterations=0,
                         surface_clearance=.03, dry_run=False):
    """Project initially-inside clothing vertices just outside the body.

    All proposals are computed before writing any coordinate.  A proposed move
    beyond ``max_displacement`` or an unresolved inside vertex raises
    :class:`FitLimitError` with a detailed report and leaves every object alone.
    Existing shape keys beyond Basis are refused: changing only Basis would
    silently invalidate their absolute targets.  Shared mesh datablocks are also
    refused; pass independent export copies rather than source/reference data.

    ``dry_run=True`` returns the same proposed metrics without applying them.
    With the default ``surface_iterations=0``, surface sample penetrations are
    reported without moving originally-outside vertices.  Opting into surface
    iterations permits minimal edge/centroid constraint corrections on their
    vertices too.  This keeps topology intact and addresses coarse triangles
    crossing the body between outside corners.  It is still a sampled rest-pose
    check, not an animation or full continuous collision guarantee.
    """
    if not (0 <= clearance <= max_displacement and math.isfinite(max_displacement)
            and max_displacement > 0 and 1 <= max_iterations <= 20):
        raise ValueError('Invalid clearance, displacement limit, or iteration count')
    if not (0 <= surface_iterations <= 30 and 0 <= surface_clearance <= max_displacement):
        raise ValueError('Invalid surface constraint options')
    objects = list(clothing_objects)
    if not objects or len(set(objects)) != len(objects):
        raise ValueError('Pass a nonempty list of distinct clothing objects')
    body_digest = _position_digest(body)
    bvh, body_info = _build_body_bvh(body)
    report = {'body': body.name, 'body_sha256': body_digest,
              'body_surface': body_info, 'clearance_cm': clearance,
              'max_displacement_cm': max_displacement,
              'surface_iterations': surface_iterations,
              'surface_clearance_cm': surface_clearance,
              'applied': False, 'objects': []}
    proposals = []
    limit_violations = []
    unresolved = []
    unresolved_surfaces = []
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
        inside_indices = [i for i, point in enumerate(original)
                          if _nearest(bvh, point)[2] < -penetration_tolerance]
        for _ in range(max_iterations):
            remaining = 0
            for index in inside_indices:
                location, normal, signed, _ = _nearest(bvh, fitted[index])
                if signed < clearance - penetration_tolerance:
                    fitted[index] = location + normal * clearance
                    remaining += 1
            if remaining == 0:
                break
        surface_passes = (_project_surface_constraints(
            bvh, obj, fitted, surface_iterations, surface_clearance,
            penetration_tolerance) if surface_iterations else [])
        moved_indices = [i for i in range(len(fitted))
                         if (fitted[i] - original[i]).length > 1e-7]
        travels = [(fitted[i] - original[i]).length for i in moved_indices]
        too_far = [int(i) for i, distance in zip(moved_indices, travels)
                   if distance > max_displacement]
        before = _summary(bvh, original, penetration_tolerance)
        after = _summary(bvh, fitted, penetration_tolerance)
        item = {'object': obj.name, 'mesh': obj.data.name,
                'initially_inside_vertices': len(inside_indices),
                'changed_vertices': len(moved_indices),
                'outside_vertices_adjusted_for_surface_constraints': len(
                    set(moved_indices) - set(inside_indices)),
                'surface_constraint_passes': surface_passes,
                'max_travel_cm': max(travels, default=0.0),
                'travel_p95_cm': _percentile(travels, .95),
                'over_limit_vertices': len(too_far),
                'over_limit_vertex_sample': too_far[:20],
                'before': before, 'after': after,
                'surface_samples_before': _surface_samples(bvh, obj, original, penetration_tolerance),
                'surface_samples_after': _surface_samples(bvh, obj, fitted, penetration_tolerance)}
        report['objects'].append(item)
        proposals.append((obj, moved_indices, fitted))
        if too_far:
            limit_violations.append(obj.name)
        if after['inside_vertices']:
            unresolved.append(obj.name)
        if surface_iterations and item['surface_samples_after']['inside_samples']:
            unresolved_surfaces.append(obj.name)
    if limit_violations:
        raise FitLimitError('Proposed clothing fit exceeds displacement limit: '
                            + ', '.join(limit_violations), report)
    if unresolved:
        raise FitLimitError('Clothing vertices remain inside the body: '
                            + ', '.join(unresolved), report)
    if unresolved_surfaces:
        raise FitLimitError('Clothing edge/face samples remain inside the body: '
                            + ', '.join(unresolved_surfaces), report)
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
