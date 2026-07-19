import cadquery as cq

# Example agent-authored model: 120 mm OD, 90 mm ID, 12 mm tall.
result = cq.Workplane("XY").circle(60).circle(45).extrude(12)
show_object(result, name="ring_brim")
