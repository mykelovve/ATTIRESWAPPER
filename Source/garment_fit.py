"""Cap-aware, conservative Blender garment fitting in world-space centimetres.

Requires fit_helpers.py. Does not export files or edit body parts, topology,
UVs, materials, weights or modifiers. These sampled rest-pose checks do not
validate animation, cloth physics or game compatibility.
"""
import hashlib, math, struct
import bpy
from mathutils import Vector
from fit_helpers import fit_clothing_to_body, FitLimitError


def _dense_surface_constraints(obj):
    """Sample vertices, edge fifths and an interior triangle fifths lattice."""
    constraints=[((v.index,),(1.,)) for v in obj.data.vertices]
    for e in obj.data.edges:
        for step in range(1,5):
            t=step/5
            constraints.append((tuple(e.vertices),(1-t,t)))
    obj.data.calc_loop_triangles()
    for tri in obj.data.loop_triangles:
        for a in range(1,4):
            for b in range(1,5-a):
                c=5-a-b
                if c>0:constraints.append((tuple(tri.vertices),(a/5,b/5,c/5)))
        constraints.append((tuple(tri.vertices),(1/3,1/3,1/3)))
    return constraints


def _digest(obj):
    h=hashlib.sha256()
    for v in obj.data.vertices:h.update(struct.pack('<3f',*v.co))
    return h.hexdigest()


def detect_internal_cap_centers(obj):
    """Find closed planar triangle fans used as internal finger/cuff caps.

    A cap has degree >=10, one triangular face per ring neighbor, a single
    closed ring, center within .25 mean ring radii of its average, and RMS
    plane residual <=.10 mean radii. Ordinary curved garment vertices do not
    qualify. Uncertain fans remain unchanged by this detector; a subsequent
    fit exceeding the travel limit fails before any garment is written.
    """
    mesh=obj.data
    neighbors=[set() for _ in mesh.vertices]
    incident=[[] for _ in mesh.vertices]
    for e in mesh.edges:
        a,b=e.vertices;neighbors[a].add(b);neighbors[b].add(a)
    for p in mesh.polygons:
        for i in p.vertices:incident[i].append(tuple(p.vertices))
    found=[];evidence=[]
    for i,ring in enumerate(neighbors):
        if len(ring)<10 or len(incident[i])!=len(ring):continue
        if any(len(face)!=3 for face in incident[i]):continue
        links={j:set() for j in ring}
        for face in incident[i]:
            a,b=[j for j in face if j!=i];links[a].add(b);links[b].add(a)
        if any(len(edges)!=2 for edges in links.values()):continue
        # Verify one ring instead of two coincident fans.
        seen=set();todo=[next(iter(ring))]
        while todo:
            j=todo.pop()
            if j in seen:continue
            seen.add(j);todo.extend(links[j]-seen)
        if seen!=ring:continue
        points=[obj.matrix_world@mesh.vertices[j].co for j in ring]
        center=sum(points,Vector())/len(points)
        offsets=[p-center for p in points]
        radius=sum(p.length for p in offsets)/len(offsets)
        if radius<1e-6:continue
        offset=((obj.matrix_world@mesh.vertices[i].co)-center).length/radius
        if offset>.25:continue
        # The area normal of the original consistently wound fan is stable
        # even when the center is slightly offset within the closure disk.
        normal=Vector()
        for face in incident[i]:
            a,b,c=[obj.matrix_world@mesh.vertices[j].co for j in face]
            normal+=(b-a).cross(c-a)
        if normal.length<1e-8:continue
        normal.normalize()
        rms=math.sqrt(sum(p.dot(normal)**2 for p in offsets)/len(offsets))/radius
        if rms>.10:continue
        found.append(i)
        evidence.append({'vertex':i,'degree':len(ring),'center_offset_radius_ratio':offset,'plane_rms_radius_ratio':rms})
    return found,evidence


def make_body_collider(body_parts):
    """Make a disposable combined body/head collider with identity transform."""
    vertices=[];triangles=[]
    for obj in body_parts:
        if obj.type!='MESH':raise TypeError('Body collider parts must be meshes')
        offset=len(vertices)
        vertices.extend(obj.matrix_world@v.co for v in obj.data.vertices)
        obj.data.calc_loop_triangles()
        triangles.extend(tuple(offset+i for i in t.vertices) for t in obj.data.loop_triangles)
    mesh=bpy.data.meshes.new('_GarmentBodyCollider')
    mesh.from_pydata(vertices,[],triangles);mesh.update()
    obj=bpy.data.objects.new('_GarmentBodyCollider',mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def fit_garments(body_parts, garments, *, clearance=.22, max_displacement=2,
                 surface_clearance=.12, surface_iterations=20,
                 cap_centers=None, auto_detect_caps=True, dry_run=False,
                 dense_surface_samples=True):
    """Propose every garment atomically, then write clothing positions only.

    cap_centers optionally maps object names to explicit vertex-index lists.
    Detection applies otherwise; pass auto_detect_caps=False for open coats.
    Body parts should include both body and head, in their common rest pose.
    Shared meshes and non-Object mode are refused. Shape keys are kept: every
    key receives the same offset as Basis, so each key's shape is unchanged.
    No vertex moves further than max_displacement; a garment that cannot be
    fully repaired within it is still repaired where possible and reported
    with fully_resolved False. Interior items (interior_item) are unchanged.
    Internal cap faces/spokes are excluded from exterior collision metrics.
    The original cap center offset is preserved by mean ring displacement.
    Dense surface constraints additionally check vertices, edge fifths and
    triangle fifths lattices; use .12cm surface clearance for tight armwear.
    """
    body_parts=list(body_parts);garments=list(garments)
    if not body_parts or not garments:raise ValueError('Explicit body and garment meshes are required')
    if len(set(garments))!=len(garments):raise ValueError('Garments must be distinct')
    if set(body_parts)&set(garments):raise ValueError('Body parts cannot be fitted as garments')
    body_digests={o.name:_digest(o) for o in body_parts}
    collider=make_body_collider(body_parts)
    proposals=[];reports=[]
    try:
        for obj in garments:
            if obj.type!='MESH' or obj.mode!='OBJECT':raise ValueError('Garment must be an Object mode mesh')
            if obj.data.users>1:raise ValueError('Garment must have independent mesh data')
            if any(obj.data is b.data for b in body_parts):raise ValueError('Garment shares body data')
            explicit=(cap_centers or {}).get(obj.name)
            evidence=[]
            if explicit is not None:centers=list(explicit)
            elif auto_detect_caps:centers,evidence=detect_internal_cap_centers(obj)
            else:centers=[]
            centers=set(centers)
            original=[obj.matrix_world@v.co for v in obj.data.vertices]
            if any(i<0 or i>=len(original) for i in centers):raise ValueError('Invalid cap index')
            indices=[i for i in range(len(original)) if i not in centers]
            remap={i:j for j,i in enumerate(indices)}
            faces=[tuple(remap[i] for i in p.vertices) for p in obj.data.polygons if not set(p.vertices)&centers]
            mesh=bpy.data.meshes.new('_GarmentVisibleShell')
            mesh.from_pydata([original[i] for i in indices],[],faces);mesh.update()
            shell=bpy.data.objects.new('_GarmentVisibleShell',mesh)
            bpy.context.scene.collection.objects.link(shell)
            try:
                result=fit_clothing_to_body(collider,[shell],clearance=clearance,
                    max_displacement=max_displacement,surface_iterations=surface_iterations,
                    surface_clearance=surface_clearance,
                    surface_constraints=_dense_surface_constraints if dense_surface_samples else None)
                shell_item=result['objects'][0]
                fitted={i:shell.data.vertices[j].co.copy() for i,j in remap.items()}
                for center in centers:
                    ring=set()
                    for e in obj.data.edges:
                        if center in e.vertices:ring.update(set(e.vertices)-{center})
                    if not ring or ring&centers:raise ValueError('Cap ring is empty or contains another cap center')
                    delta=sum((fitted[i]-original[i] for i in ring),Vector())/len(ring)
                    fitted[center]=original[center]+delta
                travel=max(((fitted[i]-original[i]).length for i in fitted),default=0)
                if travel>max_displacement+1e-3:raise FitLimitError('Cap transfer exceeds displacement limit',result)
                result.update({'source_object':obj.name,'source_mesh':obj.data.name,
                    'excluded_internal_cap_centers':sorted(centers),'cap_detection_evidence':evidence,
                    'max_complete_mesh_travel_cm':travel,
                    'cap_policy':'Fit visible shell; exclude cap fans/spokes; preserve center offset by mean ring displacement',
                    'dense_surface_samples':dense_surface_samples,
                    'fully_resolved':shell_item['fully_resolved'],'interior_item':shell_item['interior_item'],
                    'unresolved_inside_vertices':shell_item['unresolved_inside_vertices'],
                    'unresolved_surface_samples':shell_item['surface_samples_after']['inside_samples'],
                    'deepest_remaining_penetration_cm':max(shell_item['deepest_remaining_penetration_cm'],
                        shell_item['surface_samples_after']['deepest_sample_penetration']),
                    'shape_keys_offset_with_basis':bool(obj.data.shape_keys and len(obj.data.shape_keys.key_blocks)>1),
                    'applied':not dry_run,'sampled_rest_pose_only':True})
                reports.append(result);proposals.append((obj,fitted,original))
            finally:
                bpy.data.objects.remove(shell,do_unlink=True);bpy.data.meshes.remove(mesh)
        if not dry_run:
            for obj,fitted,original in proposals:
                inverse=obj.matrix_world.inverted()
                keys=obj.data.shape_keys.key_blocks if obj.data.shape_keys else ()
                for i,p in fitted.items():
                    # Untouched vertices keep their exact source coordinates.
                    if (p-original[i]).length<=1e-7:continue
                    vertex=obj.data.vertices[i]
                    delta=inverse@p-vertex.co
                    vertex.co=vertex.co+delta
                    # Shape keys store absolute positions. Offsetting every key
                    # like Basis keeps each key's shape relative to Basis intact.
                    for key in keys:key.data[i].co=key.data[i].co+delta
                obj.data.update()
        assert all(_digest(o)==body_digests[o.name] for o in body_parts),'Body geometry changed'
        return {'applied':not dry_run,'body_parts':list(body_digests),'objects':reports,
                'limits':'Sampled rest-pose garment geometry only; animation and game physics unverified.'}
    finally:
        mesh=collider.data;bpy.data.objects.remove(collider,do_unlink=True);bpy.data.meshes.remove(mesh)
