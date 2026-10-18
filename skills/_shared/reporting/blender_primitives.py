"""Scientific Blender primitives; import from Blender's Python environment."""
import math
import bpy
from mathutils import Vector

def material(name,color,*,glass=False):
    m=bpy.data.materials.new(name);m.use_nodes=True
    bsdf=m.node_tree.nodes.get('Principled BSDF');bsdf.inputs['Base Color'].default_value=(*color,1)
    bsdf.inputs['Roughness'].default_value=.12 if glass else .32
    if glass:
        bsdf.inputs['Transmission Weight'].default_value=1;bsdf.inputs['IOR'].default_value=1.46
    return m

def segment(a,b,radius,mat,name='connector'):
    a,b=Vector(a),Vector(b);delta=b-a
    bpy.ops.mesh.primitive_cylinder_add(vertices=24,radius=radius,depth=delta.length,location=(a+b)/2)
    obj=bpy.context.object;obj.name=name;obj.rotation_euler=delta.to_track_quat('Z','Y').to_euler();obj.data.materials.append(mat)
    return obj

def electron(position,*,radius=.18,spin=None):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32,ring_count=20,radius=radius,location=position)
    sphere=bpy.context.object;sphere.name='electron';sphere.data.materials.append(material('electron yellow',(1,.72,.03)))
    bpy.ops.object.shade_smooth()
    if spin:
        axis=Vector(spin).normalized();center=Vector(position);mat=material('spin',(0.65,.08,.04))
        a=center-axis*radius*1.8;b=center+axis*radius*1.8
        segment(a,b,.025,mat,'central spin arrow')
        bpy.ops.mesh.primitive_cone_add(vertices=24,radius1=.075,radius2=0,depth=.18,location=b+axis*.06)
        obj=bpy.context.object;obj.rotation_euler=axis.to_track_quat('Z','Y').to_euler();obj.data.materials.append(mat)
    return sphere

def interaction(a,b,*,amplitude=.07,turns=5,radius=.018):
    a,b=Vector(a),Vector(b);axis=(b-a).normalized();normal=axis.cross(Vector((0,0,1)))
    if normal.length<.01:normal=axis.cross(Vector((0,1,0)))
    normal.normalize();curve=bpy.data.curves.new('wavy interaction','CURVE');curve.dimensions='3D';curve.bevel_depth=radius;curve.bevel_resolution=3
    points=curve.splines.new('POLY');points.points.add(160)
    for i,p in enumerate(points.points):
        t=i/160;v=a+(b-a)*t+normal*amplitude*math.sin(2*math.pi*turns*t);p.co=(*v,1)
    obj=bpy.data.objects.new('wavy interaction',curve);bpy.context.collection.objects.link(obj);obj.data.materials.append(material('interaction',(.3,.16,.6)))
    return obj

def prism(center=(0,0,0),scale=1):
    vertices=[(-.5,-.4,-.5),(.5,-.4,-.5),(0,.45,-.5),(-.5,-.4,.5),(.5,-.4,.5),(0,.45,.5)]
    vertices=[tuple(center[i]+scale*p[i] for i in range(3)) for p in vertices]
    faces=[(0,2,1),(3,4,5),(0,1,4,3),(1,2,5,4),(2,0,3,5)]
    mesh=bpy.data.meshes.new('glass prism facets');mesh.from_pydata(vertices,[],faces);mesh.update()
    obj=bpy.data.objects.new('glass prism',mesh);bpy.context.collection.objects.link(obj);obj.data.materials.append(material('clear glass',(.94,.98,1),glass=True))
    edge_mat=material('glass facet edges',(.35,.52,.6))
    for edge in mesh.edges:segment(vertices[edge.vertices[0]],vertices[edge.vertices[1]],scale*.006,edge_mat,'prism facet edge')
    return obj
