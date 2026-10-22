"""Chained cubic Bézier handles materialized as one C1 NURBS curve."""
from __future__ import annotations
import rhino3dm as r3

def make_handle_curve(values,closed=False):
    values=[tuple(map(float,p)) for p in values]
    if len(values)<4 or len(values)%2: raise ValueError("pick a handle for every curve point")
    anchors=values[0::2];handles=values[1::2]
    if len(anchors)<2: raise ValueError("at least two curve points are required")
    segments=len(anchors) if closed else len(anchors)-1;controls=[]
    for i in range(segments):
        j=(i+1)%len(anchors);a0,a1=anchors[i],anchors[j];h0,h1=handles[i],handles[j]
        incoming=tuple(2*a1[k]-h1[k] for k in range(3));span=(a0,h0,incoming,a1)
        controls.extend(span if i==0 else span[1:])
    curve=r3.NurbsCurve(3,False,4,len(controls))
    for i,p in enumerate(controls):curve.Points[i]=r3.Point4d(*p,1.0)
    knots=[0.0]*3
    for boundary in range(1,segments):knots.extend([boundary/segments]*3)
    knots.extend([1.0]*3)
    for i,knot in enumerate(knots):curve.Knots[i]=knot
    if not curve.IsValid:raise ValueError("handle curve construction produced an invalid NURBS")
    return curve
