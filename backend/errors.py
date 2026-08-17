"""Shared exception types with no dependencies on the job or handler layers.

A leaf module so `backend/plan/render_plan.py` (rendering) and
`backend/worker.py` (job queue orchestration) can both raise/catch the same
`Cancelled` without either layer importing the other. Before this existed,
render_plan.py imported worker.py solely to reach this class, which
re-coupled the plan layer to the job layer in exactly the direction an
earlier commit deliberately decoupled.
"""


class Cancelled(Exception):
    """Raised by a handler (or the render pipeline it drives) when it
    notices the job has been cancel-requested."""
