import cadquery as cq

# Example agent-authored 110 x 35 x 28 mm right-triangular wedge.
result = cq.Workplane("XZ").polyline([(0, 0), (110, 0), (110, 28)]).close().extrude(35).translate((0, 17.5, 0))
show_object(result, name="door_stopper")
